"""Geometry-first association of source-local tracks in explicit camera groups.

The implementation is deliberately bounded and deterministic.  It associates one
newly completed source sample at a time against persisted fused current entities,
using world distance, time, short trajectory prediction, and configured topology.
Raw worker identities are never rewritten.
"""
from __future__ import annotations

import json
import math
import uuid

from .. import db


def _new_fused_id() -> str:
    """Create an opaque runtime identity; offline fixtures patch this deterministically."""
    return "F" + uuid.uuid4().hex[:16]


def _groups_for_source(source_id: int) -> list[dict]:
    return [row for row in db.q("SELECT * FROM multiview_groups WHERE enabled=1")
            if source_id in db.jload(row["source_ids_json"], [])]


def _compatible(group: dict, source_id: int, evidence: list[dict]) -> bool:
    topology = db.jload(group.get("topology_json"), {})
    neighbors = topology.get("neighbors") or {}
    if not neighbors:
        return True
    other_sources = {int(item["source_id"]) for item in evidence}
    allowed = {int(value) for value in neighbors.get(str(source_id), [])}
    return not other_sources or bool(other_sources & allowed)


def _predicted_position(fused_id: str, at_ts: float, fallback: tuple[float, float]) -> tuple[float, float]:
    rows = db.q(
        "SELECT ts,x_map,y_map FROM fused_observations WHERE fused_entity_id=? "
        "ORDER BY ts DESC,id DESC LIMIT 2", (fused_id,))
    if len(rows) < 2 or rows[0]["ts"] <= rows[1]["ts"]:
        return fallback
    dt = rows[0]["ts"] - rows[1]["ts"]
    vx = (rows[0]["x_map"] - rows[1]["x_map"]) / dt
    vy = (rows[0]["y_map"] - rows[1]["y_map"]) / dt
    horizon = max(0.0, min(at_ts - rows[0]["ts"], 2.0))
    return rows[0]["x_map"] + vx * horizon, rows[0]["y_map"] + vy * horizon


def minimum_cost_assignment(costs: list[list[float]], invalid: float = math.inf) -> list[tuple[int, int, float]]:
    """Globally minimize a small rectangular assignment using dynamic programming.

    Active camera groups are intentionally bounded; this exact solver avoids the
    unstable decisions of greedy nearest-neighbour matching without adding a
    heavyweight runtime dependency. Unmatched rows have zero cost and are allowed.
    """
    if not costs or not costs[0]:
        return []
    memo: dict[tuple[int, int], tuple[float, tuple]] = {}

    def solve(row: int, used: int) -> tuple[float, tuple]:
        key = (row, used)
        if key in memo:
            return memo[key]
        if row == len(costs):
            return 0.0, ()
        best_cost, best_pairs = solve(row + 1, used)
        for col, value in enumerate(costs[row]):
            if used & (1 << col) or not math.isfinite(value):
                continue
            tail_cost, tail_pairs = solve(row + 1, used | (1 << col))
            # A valid association receives a gate-sized reward, so the global
            # optimum prefers compatible matches while still minimizing cost.
            total = value - 1_000_000.0 + tail_cost
            candidate = ((row, col, value),) + tail_pairs
            if total < best_cost or (total == best_cost and candidate < best_pairs):
                best_cost, best_pairs = total, candidate
        memo[key] = best_cost, best_pairs
        return memo[key]

    return list(solve(0, 0)[1])


def process_completed_sample(sample: dict) -> None:
    for group in _groups_for_source(sample["source_id"]):
        _process_group_sample(group, sample)


def process_completed_samples(samples: list[dict]) -> None:
    """Process one ingestion batch and refresh each affected fused view once.

    Source-local association still runs independently for every completed sample.
    Deferring the read-model refresh until all synchronized camera samples have
    contributed avoids rebuilding the same group several times per video frame.
    """
    with db.transaction():
        affected: dict[tuple[int, str], tuple[dict, float]] = {}
        for sample in samples:
            for group in _groups_for_source(sample["source_id"]):
                _process_group_sample(group, sample, refresh=False)
                key = (group["id"], sample["entity_type"])
                previous = affected.get(key)
                if previous is None or sample["timestamp"] > previous[1]:
                    affected[key] = (group, sample["timestamp"])
        for (_group_id, entity_type), (group, timestamp) in affected.items():
            _refresh_group_current(group, entity_type, timestamp)


def _process_group_sample(group: dict, sample: dict, refresh: bool = True) -> None:
    source_id = sample["source_id"]
    entity_type = sample["entity_type"]
    timestamp = sample["timestamp"]
    local = db.q(
        "SELECT * FROM source_current_entities WHERE source_id=? AND entity_type=? "
        "AND sample_key=? AND x_map IS NOT NULL AND y_map IS NOT NULL ORDER BY event_id",
        (source_id, entity_type, sample["sample_key"]),
    )
    active = db.q(
        "SELECT * FROM fused_current_entities WHERE group_id=? AND entity_type=? "
        "AND ts>=? ORDER BY fused_entity_id",
        (group["id"], entity_type, timestamp - group["track_age_s"]),
    )

    already: dict[int, str] = {}
    unmatched = []
    for index, row in enumerate(local):
        member = db.q1(
            "SELECT m.fused_entity_id FROM fused_entity_members m JOIN fused_entities f "
            "ON f.id=m.fused_entity_id WHERE f.group_id=? AND m.source_id=? "
            "AND COALESCE(m.worker_id,-1)=COALESCE(?,-1) AND m.local_entity_id=? "
            "AND f.ended_at IS NULL ORDER BY m.last_seen_at DESC LIMIT 1",
            (group["id"], source_id, row.get("worker_id"), row.get("local_entity_id")),
        )
        if member:
            already[index] = member["fused_entity_id"]
        else:
            unmatched.append(index)

    candidates = []
    for fused in active:
        evidence = db.jload(fused["member_evidence_json"], [])
        if any(int(item["source_id"]) == source_id for item in evidence):
            continue
        if not _compatible(group, source_id, evidence):
            continue
        candidates.append(fused)

    costs = []
    for index in unmatched:
        row = local[index]
        row_costs = []
        for fused in candidates:
            dt = abs(timestamp - fused["ts"])
            if dt > group["time_tolerance_s"]:
                row_costs.append(math.inf); continue
            px, py = _predicted_position(
                fused["fused_entity_id"], timestamp, (fused["x_map"], fused["y_map"]))
            distance = math.hypot(row["x_map"] - px, row["y_map"] - py)
            if distance > group["spatial_gate_m"]:
                row_costs.append(math.inf); continue
            row_costs.append(distance + 0.25 * dt / max(group["time_tolerance_s"], 1e-6))
        costs.append(row_costs)
    assignments = minimum_cost_assignment(costs)
    for row_index, candidate_index, cost in assignments:
        already[unmatched[row_index]] = candidates[candidate_index]["fused_entity_id"]
    assignment_costs = {
        unmatched[row_index]: cost for row_index, _candidate_index, cost
        in assignments
    }

    for index, row in enumerate(local):
        fused_id = already.get(index)
        cost = assignment_costs.get(index)
        if fused_id is None:
            fused_id = _new_fused_id()
            db.ex(
                "INSERT INTO fused_entities (id,group_id,entity_type,algorithm,algorithm_version,"
                "configuration_revision,space_revision_id,created_at,last_seen_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (fused_id, group["id"], entity_type, group["algorithm"], group["algorithm_version"],
                 group["configuration_revision"], db.current_space_revision_id(), timestamp, timestamp),
            )
        db.ex(
            "INSERT INTO fused_entity_members (fused_entity_id,source_id,worker_id,local_entity_id,"
            "sample_key,source_event_id,joined_at,last_seen_at,association_cost) VALUES (?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(fused_entity_id,source_id,worker_id,local_entity_id) DO UPDATE SET "
            "sample_key=excluded.sample_key,source_event_id=excluded.source_event_id,"
            "last_seen_at=excluded.last_seen_at,association_cost=excluded.association_cost",
            (fused_id, source_id, row.get("worker_id") if row.get("worker_id") is not None else -1,
             row.get("local_entity_id") or f"event-{row['event_id']}",
             row["sample_key"], row["event_id"], timestamp, timestamp, cost),
        )
        db.ex("UPDATE fused_entities SET last_seen_at=?,ended_at=NULL WHERE id=?", (timestamp, fused_id))

    if refresh:
        _refresh_group_current(group, entity_type, timestamp)


def _refresh_group_current(group: dict, entity_type: str, as_of: float,
                           record_history: bool = True) -> None:
    source_ids = db.jload(group["source_ids_json"], [])
    fresh_since = as_of - group["track_age_s"]
    entities = db.q(
        "SELECT * FROM fused_entities WHERE group_id=? AND entity_type=? AND ended_at IS NULL",
        (group["id"], entity_type),
    )
    active_ids = []
    for fused in entities:
        members = db.q(
            "SELECT m.*,c.x_map,c.y_map,c.zone_id,c.confidence,c.ts FROM fused_entity_members m "
            "JOIN source_current_entities c ON c.source_id=m.source_id "
            "AND COALESCE(c.worker_id,-1)=COALESCE(m.worker_id,-1) "
            "AND COALESCE(c.local_entity_id,'')=COALESCE(m.local_entity_id,'') "
            "AND c.sample_key=m.sample_key WHERE m.fused_entity_id=? AND c.ts>=? "
            "AND c.x_map IS NOT NULL AND c.y_map IS NOT NULL ORDER BY c.source_id",
            (fused["id"], fresh_since),
        )
        if not members:
            db.ex("UPDATE fused_entities SET ended_at=? WHERE id=?", (as_of, fused["id"]))
            db.ex("DELETE FROM fused_current_entities WHERE fused_entity_id=?", (fused["id"],))
            continue
        active_ids.append(fused["id"])
        weights = [max(0.05, float(member.get("confidence") or 1.0)) for member in members]
        total = sum(weights)
        x_map = sum(member["x_map"] * weight for member, weight in zip(members, weights)) / total
        y_map = sum(member["y_map"] * weight for member, weight in zip(members, weights)) / total
        zones = [member["zone_id"] for member in members if member.get("zone_id") is not None]
        zone_id = max(set(zones), key=lambda value: (zones.count(value), -value)) if zones else None
        evidence = [{
            "source_id": member["source_id"],
            "worker_id": None if member.get("worker_id") == -1 else member.get("worker_id"),
            "local_entity_id": member["local_entity_id"], "source_event_id": member.get("source_event_id"),
            "sample_key": member["sample_key"], "point_map": [member["x_map"], member["y_map"]],
        } for member in members]
        confidence = sum(weights) / len(weights)
        payload = json.dumps(evidence, sort_keys=True)
        ts = max(member["ts"] for member in members)
        db.ex(
            "INSERT INTO fused_current_entities (fused_entity_id,group_id,entity_type,ts,x_map,y_map,"
            "zone_id,confidence,quality,member_evidence_json,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(fused_entity_id) DO UPDATE SET ts=excluded.ts,x_map=excluded.x_map,"
            "y_map=excluded.y_map,zone_id=excluded.zone_id,confidence=excluded.confidence,"
            "quality=excluded.quality,member_evidence_json=excluded.member_evidence_json,updated_at=excluded.updated_at",
            (fused["id"], group["id"], entity_type, ts, x_map, y_map, zone_id, confidence,
             "known", payload, db.now()),
        )
        if record_history:
            db.ex(
                "INSERT INTO fused_observations (fused_entity_id,group_id,ts,x_map,y_map,zone_id,confidence,"
                "quality,member_evidence_json,algorithm,algorithm_version,configuration_revision,space_revision_id,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (fused["id"], group["id"], ts, x_map, y_map, zone_id, confidence, "known", payload,
                 group["algorithm"], group["algorithm_version"], group["configuration_revision"],
                 db.current_space_revision_id(), db.now()),
            )

    placeholders = ",".join("?" for _ in source_ids)
    samples = db.q(
        f"SELECT source_id,ts FROM source_current_samples WHERE entity_type=? AND source_id IN ({placeholders})",
        (entity_type, *source_ids),
    ) if source_ids else []
    fresh_sources = {row["source_id"] for row in samples if row["ts"] >= fresh_since}
    quality = "known" if len(fresh_sources) == len(source_ids) else ("partial" if fresh_sources else "unknown")
    zone_ids = [row["id"] for row in db.q("SELECT id FROM zones")]
    for zone_id in zone_ids:
        value = db.q1(
            "SELECT COUNT(*) n FROM fused_current_entities WHERE group_id=? AND entity_type=? AND zone_id=?",
            (group["id"], entity_type, zone_id),
        )["n"]
        db.ex(
            "INSERT INTO zone_current_occupancy (group_id,zone_id,entity_type,value,quality,as_of,provenance_json) "
            "VALUES (?,?,?,?,?,?,?) ON CONFLICT(group_id,zone_id,entity_type) DO UPDATE SET "
            "value=excluded.value,quality=excluded.quality,as_of=excluded.as_of,provenance_json=excluded.provenance_json",
            (group["id"], zone_id, entity_type, value, quality, as_of,
             json.dumps({"fresh_source_ids": sorted(fresh_sources), "source_ids": source_ids,
                         "fused_entity_ids": active_ids})),
        )
        if record_history:
            db.ex(
                "INSERT INTO zone_occupancy_observations "
                "(group_id,zone_id,entity_type,ts,value,quality,provenance_json,space_revision_id,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(group_id,zone_id,entity_type,ts) DO UPDATE SET "
                "value=excluded.value,quality=excluded.quality,provenance_json=excluded.provenance_json",
                (group["id"], zone_id, entity_type, as_of, value, quality,
                 json.dumps({"fresh_source_ids": sorted(fresh_sources), "source_ids": source_ids,
                             "fused_entity_ids": active_ids}), db.current_space_revision_id(), db.now()),
            )


def refresh_freshness(now: float | None = None) -> None:
    """Periodic freshness transition; never manufactures an observed zero."""
    as_of = db.now() if now is None else now
    for group in db.q("SELECT * FROM multiview_groups WHERE enabled=1"):
        entity_types = [row["entity_type"] for row in db.q(
            "SELECT DISTINCT entity_type FROM source_current_samples WHERE source_id IN ("
            + ",".join("?" for _ in db.jload(group["source_ids_json"], [])) + ")",
            tuple(db.jload(group["source_ids_json"], [])),
        )] if db.jload(group["source_ids_json"], []) else []
        for entity_type in entity_types:
            _refresh_group_current(group, entity_type, as_of, record_history=False)
