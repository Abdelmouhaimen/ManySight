"""Geometry-first association of source-local tracks in explicit camera groups.

The implementation is deliberately bounded and deterministic.  It associates the
freshest completed source samples against persisted fused current entities, using
world distance, time, short trajectory prediction, and configured topology.  Raw
worker identities are never rewritten.

Scheduling lives in services/realtime.py.  This module exposes the unit of work
that scheduler drives — one *group tick*: take the freshest eligible sample from
each source in a group, run each source's association stage in deterministic
order, and refresh the group's current state once.  A stage is exactly what a
single arriving sample used to do, so coalescing several camera frames into one
tick changes how often fusion runs, never how it decides.

Every SQL lookup that used to run per local entity or per candidate pair is
preloaded once per stage; the association arithmetic itself is unchanged.
"""
from __future__ import annotations

import json
import math
import uuid

from .. import db
from . import config_cache
from .metrics import registry


def _new_fused_id() -> str:
    """Create an opaque runtime identity; offline fixtures patch this deterministically."""
    return "F" + uuid.uuid4().hex[:16]


def _groups_for_source(source_id: int) -> list[dict]:
    """Enabled groups containing this source, from the configuration cache."""
    return config_cache.groups_for_source(source_id)


def _compatible(group: dict, source_id: int, evidence: list[dict]) -> bool:
    neighbors = group.get("neighbors")
    if neighbors is None:
        neighbors = db.jload(group.get("topology_json"), {}).get("neighbors") or {}
    if not neighbors:
        return True
    other_sources = {int(item["source_id"]) for item in evidence}
    allowed = {int(value) for value in neighbors.get(str(source_id), [])}
    return not other_sources or bool(other_sources & allowed)


def _load_recent_fused_observations(fused_ids: list[str]) -> dict[str, list[dict]]:
    """The two most recent observations per fused entity, in one bounded query.

    Replaces a `SELECT ... LIMIT 2` per (local entity x candidate) pair. Ordering
    matches the per-pair query it replaces: `ts DESC, id DESC`.
    """
    if not fused_ids:
        return {}
    placeholders = ",".join("?" for _ in fused_ids)
    rows = db.q(
        "SELECT fused_entity_id,ts,x_map,y_map FROM (SELECT fused_entity_id,ts,x_map,y_map,"
        " ROW_NUMBER() OVER (PARTITION BY fused_entity_id ORDER BY ts DESC,id DESC) rn"
        f" FROM fused_observations WHERE fused_entity_id IN ({placeholders})) WHERE rn<=2"
        " ORDER BY fused_entity_id,rn",
        tuple(fused_ids),
    )
    recent: dict[str, list[dict]] = {}
    for row in rows:
        recent.setdefault(row["fused_entity_id"], []).append(row)
    return recent


def _predicted_position(recent: list[dict], at_ts: float,
                        fallback: tuple[float, float]) -> tuple[float, float]:
    """Short constant-velocity extrapolation — unchanged, now from preloaded rows."""
    if len(recent) < 2 or recent[0]["ts"] <= recent[1]["ts"]:
        return fallback
    dt = recent[0]["ts"] - recent[1]["ts"]
    vx = (recent[0]["x_map"] - recent[1]["x_map"]) / dt
    vy = (recent[0]["y_map"] - recent[1]["y_map"]) / dt
    horizon = max(0.0, min(at_ts - recent[0]["ts"], 2.0))
    return recent[0]["x_map"] + vx * horizon, recent[0]["y_map"] + vy * horizon


def _load_existing_memberships(group_id: int, source_id: int,
                               local: list[dict]) -> dict[tuple[int, str], str]:
    """Current fused identity for each source-local track, in one query.

    Replaces one `SELECT ... LIMIT 1` per local entity. Keyed by the same
    `(worker_id normalized to -1, local_entity_id)` scope the per-row query used,
    so source-local tracker identity stays scoped to its worker run. Ties on
    `last_seen_at` resolve by member row id, making the winner deterministic
    where the single-row query left it to SQLite.
    """
    if not local:
        return {}
    wanted = {(-1 if row.get("worker_id") is None else int(row["worker_id"]),
               row.get("local_entity_id")) for row in local}
    entity_ids = sorted({key[1] for key in wanted if key[1] is not None})
    if not entity_ids:
        return {}
    placeholders = ",".join("?" for _ in entity_ids)
    rows = db.q(
        "SELECT m.id,m.worker_id,m.local_entity_id,m.fused_entity_id,m.last_seen_at "
        "FROM fused_entity_members m JOIN fused_entities f ON f.id=m.fused_entity_id "
        f"WHERE f.group_id=? AND m.source_id=? AND f.ended_at IS NULL "
        f"AND m.local_entity_id IN ({placeholders})",
        (group_id, source_id, *entity_ids),
    )
    best: dict[tuple[int, str], tuple[float, int, str]] = {}
    for row in rows:
        key = (-1 if row["worker_id"] is None else int(row["worker_id"]), row["local_entity_id"])
        if key not in wanted:
            continue
        rank = (row["last_seen_at"], row["id"])
        current = best.get(key)
        if current is None or rank > (current[0], current[1]):
            best[key] = (row["last_seen_at"], row["id"], row["fused_entity_id"])
    return {key: value[2] for key, value in best.items()}


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


# --------------------------------------------------------------------------
# Group ticks
# --------------------------------------------------------------------------

def _stage_sort_key(sample: dict) -> tuple:
    return (sample["timestamp"], sample["source_id"], sample.get("sample_key") or "")


def fuse_samples(samples: list[dict]) -> list[tuple[int, str]]:
    """Run one fusion pass over a set of completed source samples.

    Samples are routed to their groups and each group's sources are staged in
    `(timestamp, source_id, sample_key)` order, which is the order separate
    arrivals would have produced. Returns the `(group_id, entity_type)` pairs
    touched. The caller owns the transaction.
    """
    by_group: dict[tuple[int, str], tuple[dict, list[dict]]] = {}
    for sample in samples:
        for group in _groups_for_source(sample["source_id"]):
            key = (group["id"], sample["entity_type"])
            by_group.setdefault(key, (group, []))[1].append(sample)
    for key in sorted(by_group):
        group, group_samples = by_group[key]
        _fuse_group(group, key[1], sorted(group_samples, key=_stage_sort_key))
    return sorted(by_group)


def _fuse_group(group: dict, entity_type: str, samples: list[dict]) -> None:
    """One group tick: every changed source's freshest sample, then one refresh.

    Between stages, only the fused entities the stage just touched are
    recomputed. That is what makes a coalesced tick decide like separate
    arrivals: the next source sees the entity the previous source created or
    moved, and can associate with it instead of minting a duplicate identity.

    Ending stale entities and recomputing occupancy is deliberately *not* done
    between stages. Inside a tick, every source's `source_current_entities` rows
    have already been replaced, but a source that has not been staged yet still
    carries member rows pointing at its previous sample key — a full refresh
    there would read those members as gone and end perfectly live entities, and
    the next stage would have to create new identities for them.
    """
    if not samples:
        return
    last = len(samples) - 1
    for index, sample in enumerate(samples):
        with registry.timer("fusion.stage_associate_s"):
            touched = _process_group_sample(group, sample, refresh=False)
        with registry.timer("fusion.stage_refresh_s"):
            if index == last:
                _refresh_group_current(group, entity_type, sample["timestamp"])
            else:
                _refresh_entities(group, entity_type, touched, sample["timestamp"])
    registry.increment("fusion.group_ticks")
    registry.increment("fusion.source_stages", len(samples))


def run_group_tick(group: dict, entity_type: str, samples: list[dict], as_of: float) -> None:
    """One scheduled live tick for a single group.

    `samples` holds the freshest sample of every source whose state advanced
    since this group's previous tick — a source that did not change is not
    re-associated, but its current entities still contribute to the refresh. An
    empty list is a reconciliation tick: refresh current state without recording
    derived history. The caller owns the transaction.
    """
    if samples:
        _fuse_group(group, entity_type, sorted(samples, key=_stage_sort_key))
    else:
        _refresh_group_current(group, entity_type, as_of, record_history=False)


def process_completed_sample(sample: dict) -> None:
    """Fuse one completed sample synchronously (recovery and promotion paths)."""
    with db.transaction():
        fuse_samples([sample])


def process_completed_samples(samples: list[dict]) -> None:
    """Fuse a set of completed samples synchronously, in one transaction.

    The live path no longer calls this per ingested frame — services/realtime.py
    schedules group ticks instead. It remains the synchronous entry point for
    offline derivation, recovery, and tests.
    """
    if not samples:
        return
    with db.transaction():
        fuse_samples(samples)


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

    memberships = _load_existing_memberships(group["id"], source_id, local)
    already: dict[int, str] = {}
    unmatched = []
    for index, row in enumerate(local):
        key = (-1 if row.get("worker_id") is None else int(row["worker_id"]),
               row.get("local_entity_id"))
        fused_id = memberships.get(key)
        if fused_id:
            already[index] = fused_id
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

    recent = _load_recent_fused_observations(
        [fused["fused_entity_id"] for fused in candidates]) if unmatched and candidates else {}
    registry.increment("fusion.candidate_pairs", len(unmatched) * len(candidates))

    costs = []
    for index in unmatched:
        row = local[index]
        row_costs = []
        for fused in candidates:
            dt = abs(timestamp - fused["ts"])
            if dt > group["time_tolerance_s"]:
                row_costs.append(math.inf); continue
            px, py = _predicted_position(
                recent.get(fused["fused_entity_id"], []), timestamp,
                (fused["x_map"], fused["y_map"]))
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

    touched: set[str] = set()
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
        touched.add(fused_id)

    if refresh:
        _refresh_group_current(group, entity_type, timestamp)
    return touched


def _fresh_members_by_entity(fused_ids: list[str], fresh_since: float) -> dict[str, list[dict]]:
    """Fresh member evidence for every active fused entity, in one query.

    Replaces the per-entity join in `_refresh_group_current`. Ordering within an
    entity matches the per-entity query (`ORDER BY c.source_id`).
    """
    if not fused_ids:
        return {}
    placeholders = ",".join("?" for _ in fused_ids)
    rows = db.q(
        "SELECT m.*,c.x_map,c.y_map,c.zone_id,c.confidence,c.ts FROM fused_entity_members m "
        "JOIN source_current_entities c ON c.source_id=m.source_id "
        "AND COALESCE(c.worker_id,-1)=COALESCE(m.worker_id,-1) "
        "AND COALESCE(c.local_entity_id,'')=COALESCE(m.local_entity_id,'') "
        f"AND c.sample_key=m.sample_key WHERE m.fused_entity_id IN ({placeholders}) AND c.ts>=? "
        "AND c.x_map IS NOT NULL AND c.y_map IS NOT NULL ORDER BY m.fused_entity_id,c.source_id",
        (*fused_ids, fresh_since),
    )
    members: dict[str, list[dict]] = {}
    for row in rows:
        members.setdefault(row["fused_entity_id"], []).append(row)
    return members


def _write_current_entity(group: dict, entity_type: str, fused_id: str,
                          members: list[dict], record_history: bool) -> int | None:
    """Recompute and persist one fused entity from its fresh member evidence."""
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
        (fused_id, group["id"], entity_type, ts, x_map, y_map, zone_id, confidence,
         "known", payload, db.now()),
    )
    if record_history:
        db.ex(
            "INSERT INTO fused_observations (fused_entity_id,group_id,ts,x_map,y_map,zone_id,confidence,"
            "quality,member_evidence_json,algorithm,algorithm_version,configuration_revision,space_revision_id,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (fused_id, group["id"], ts, x_map, y_map, zone_id, confidence, "known", payload,
             group["algorithm"], group["algorithm_version"], group["configuration_revision"],
             db.current_space_revision_id(), db.now()),
        )
    return zone_id


def _refresh_entities(group: dict, entity_type: str, fused_ids: set[str], as_of: float) -> None:
    """Between-stage recompute of just the entities one source stage touched.

    Never ends an entity and never touches occupancy or derived history: inside a
    tick, a source that has not been staged yet still points its member rows at
    its previous sample key, so absence of fresh evidence here is not evidence of
    absence. The end-of-tick refresh makes those decisions once, correctly.
    """
    if not fused_ids:
        return
    members_by_entity = _fresh_members_by_entity(sorted(fused_ids), as_of - group["track_age_s"])
    for fused_id in sorted(fused_ids):
        members = members_by_entity.get(fused_id)
        if members:
            _write_current_entity(group, entity_type, fused_id, members, record_history=False)


def _refresh_group_current(group: dict, entity_type: str, as_of: float,
                           record_history: bool = True) -> None:
    source_ids = group.get("source_ids")
    if source_ids is None:
        source_ids = db.jload(group["source_ids_json"], [])
    fresh_since = as_of - group["track_age_s"]
    entities = db.q(
        "SELECT * FROM fused_entities WHERE group_id=? AND entity_type=? AND ended_at IS NULL",
        (group["id"], entity_type),
    )
    members_by_entity = _fresh_members_by_entity([row["id"] for row in entities], fresh_since)
    active_ids = []
    zone_counts: dict[int, int] = {}
    for fused in entities:
        members = members_by_entity.get(fused["id"], [])
        if not members:
            db.ex("UPDATE fused_entities SET ended_at=? WHERE id=?", (as_of, fused["id"]))
            db.ex("DELETE FROM fused_current_entities WHERE fused_entity_id=?", (fused["id"],))
            continue
        active_ids.append(fused["id"])
        zone_id = _write_current_entity(group, entity_type, fused["id"], members, record_history)
        if zone_id is not None:
            zone_counts[zone_id] = zone_counts.get(zone_id, 0) + 1
    registry.gauge("fusion.active_fused_entities", len(active_ids))

    placeholders = ",".join("?" for _ in source_ids)
    samples = db.q(
        f"SELECT source_id,ts FROM source_current_samples WHERE entity_type=? AND source_id IN ({placeholders})",
        (entity_type, *source_ids),
    ) if source_ids else []
    fresh_sources = {row["source_id"] for row in samples if row["ts"] >= fresh_since}
    quality = "known" if len(fresh_sources) == len(source_ids) else ("partial" if fresh_sources else "unknown")
    # Occupancy is counted from the fused current entities just written, which is
    # exactly what a `SELECT COUNT(*) FROM fused_current_entities` per zone would
    # return now that this refresh owns every row for (group, entity_type).
    provenance = json.dumps({"fresh_source_ids": sorted(fresh_sources), "source_ids": source_ids,
                             "fused_entity_ids": active_ids})
    for zone_id in [zone["id"] for zone in config_cache.geometry_context()[0]]:
        value = zone_counts.get(zone_id, 0)
        db.ex(
            "INSERT INTO zone_current_occupancy (group_id,zone_id,entity_type,value,quality,as_of,provenance_json) "
            "VALUES (?,?,?,?,?,?,?) ON CONFLICT(group_id,zone_id,entity_type) DO UPDATE SET "
            "value=excluded.value,quality=excluded.quality,as_of=excluded.as_of,provenance_json=excluded.provenance_json",
            (group["id"], zone_id, entity_type, value, quality, as_of, provenance),
        )
        if record_history:
            db.ex(
                "INSERT INTO zone_occupancy_observations "
                "(group_id,zone_id,entity_type,ts,value,quality,provenance_json,space_revision_id,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(group_id,zone_id,entity_type,ts) DO UPDATE SET "
                "value=excluded.value,quality=excluded.quality,provenance_json=excluded.provenance_json",
                (group["id"], zone_id, entity_type, as_of, value, quality, provenance,
                 db.current_space_revision_id(), db.now()),
            )


def refresh_freshness(now: float | None = None) -> None:
    """Make fused state current before it is read or polled.

    Two steps, in order: drain any live group tick the scheduler has not run yet
    (so a read never reports state older than the newest committed sample), then
    apply the periodic freshness transition. Neither step ever manufactures an
    observed zero.
    """
    from . import realtime
    realtime.coordinator.drain()
    as_of = db.now() if now is None else now
    groups = list(config_cache.group_config()["by_id"].values())
    if not groups:
        return
    with db.transaction():
        for group in groups:
            source_ids = group["source_ids"]
            entity_types = [row["entity_type"] for row in db.q(
                "SELECT DISTINCT entity_type FROM source_current_samples WHERE source_id IN ("
                + ",".join("?" for _ in source_ids) + ")",
                tuple(source_ids),
            )] if source_ids else []
            for entity_type in entity_types:
                _refresh_group_current(group, entity_type, as_of, record_history=False)
