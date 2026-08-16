"""Remove every camera and everything that exists only because of a camera.

Distinct from the two resets that already exist, and deliberately so:

* *Reinitialize space* archives geometry and starts a new space revision, but
  keeps the cameras — you re-place and re-calibrate the same hardware.
* *Reinitialize observations* clears evidence, but keeps cameras and geometry.
* *Reset cameras* removes the cameras themselves, so the workspace can be set up
  against different hardware from scratch.

What survives is the part of the workspace that describes the *building*: the
store record, the floor plan, its dimensions, and the canonical zones. A zone is
a physical region; it does not stop existing because the cameras watching it were
removed. Its camera-specific ZoneViews do, so a surviving zone keeps its
footprint and drops to zero views until new cameras are configured.

Saved queries and alert rules are user-authored, so their definitions survive —
but an enabled rule that can no longer fire is disabled here rather than left
pointing at a deleted group, and affected saved queries are reported.
"""
from __future__ import annotations

import hashlib
import json
import os

from .. import db

CONFIRMATION = "RESET CAMERAS"

# Worker statuses that mean an external process may still be running.
LIVE_WORKER_STATUSES = {"starting", "running"}

# Tables whose every row belongs to a source. Reset removes all sources, so
# these are emptied wholesale; they are listed explicitly rather than derived so
# a future source-scoped table has to be added here deliberately.
SOURCE_OWNED_TABLES = (
    "source_current_entities",
    "source_current_samples",
    "zone_views",
    "projection_surfaces",
    "camera_calibrations",
    "source_credentials",
)


def in_isolated_demo_workspace() -> bool:
    """Whether this request is routed into a guided-demo session database.

    The demo middleware swaps the whole workspace behind a ContextVar, so a
    reset issued while a demo session is active would destroy the prepared demo
    fixture instead of the user's cameras.
    """
    return os.path.abspath(db.current_db_path()) != os.path.abspath(db.DB_PATH)


def reset_token(source_ids: list[int] | None = None) -> str:
    """Fingerprint of the camera set a preview described.

    Execution may carry the token from its preview. If a camera was added or
    removed in between, the token no longer matches and the reset is refused
    rather than removing something the preview never listed.
    """
    ids = sorted(row["id"] for row in db.q("SELECT id FROM sources")) \
        if source_ids is None else sorted(source_ids)
    payload = json.dumps({"space_revision_id": db.current_space_revision_id(),
                          "source_ids": ids}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:32]


# ---------------------------------------------------------------------------
# dependent analytics
# ---------------------------------------------------------------------------

def _referenced_ids(filters: dict, key: str) -> list[int]:
    out = []
    for value in (filters or {}).get(key) or []:
        try:
            out.append(int(value))
        except (TypeError, ValueError):
            continue
    return out


def _broken_references(filters: dict, removed_sources: set[int],
                       removed_groups: set[int]) -> list[dict]:
    """References in `filters` that the removed cameras and groups invalidate.

    Zones are deliberately not checked: canonical zones survive a camera reset,
    so a zone filter stays valid.
    """
    broken = []
    for key, removed, kind in (("group_ids", removed_groups, "multiview group"),
                               ("source_ids", removed_sources, "source")):
        for identifier in _referenced_ids(filters, key):
            if identifier in removed:
                broken.append({"filter": key, "id": identifier, "kind": kind,
                               "reason": "removed by the camera reset"})
    return broken


def affected_saved_queries(removed_sources: set[int], removed_groups: set[int]) -> list[dict]:
    """Saved queries whose filters the reset breaks. Never rewritten, only reported."""
    out = []
    for row in db.q("SELECT id,name,filters_json FROM analyses WHERE visibility='visible' "
                    "ORDER BY id"):
        broken = _broken_references(db.jload(row["filters_json"], {}),
                                    removed_sources, removed_groups)
        if broken:
            out.append({"id": row["id"], "name": row["name"], "stale_references": broken})
    return out


def affected_alert_rules(removed_sources: set[int], removed_groups: set[int]) -> list[dict]:
    """Enabled rules that could not fire after the reset.

    A `query_condition` rule inherits its saved query's references; an
    `analysis_condition` rule carries its own inline filters; the compatibility
    kinds name a source directly.
    """
    stale_query_ids = {item["id"] for item in
                       affected_saved_queries(removed_sources, removed_groups)}
    out = []
    for row in db.q("SELECT * FROM alert_rules WHERE enabled=1 ORDER BY id"):
        params = db.jload(row["params_json"], {})
        broken: list[dict] = []
        if row["kind"] == "query_condition":
            query_id = params.get("query_id")
            if query_id in stale_query_ids:
                broken = [{"filter": "params.query_id", "id": query_id, "kind": "saved query",
                           "reason": "its filters reference removed cameras or groups"}]
        elif row["kind"] == "analysis_condition":
            analysis = db.jload(row.get("analysis_json"), {}) or {}
            broken = _broken_references(analysis.get("filters") or {},
                                        removed_sources, removed_groups)
        elif params.get("source_id") in removed_sources:
            broken = [{"filter": "params.source_id", "id": params.get("source_id"),
                       "kind": "source", "reason": "removed by the camera reset"}]
        if broken:
            out.append({"id": row["id"], "name": row["name"], "kind": row["kind"],
                        "stale_references": broken})
    return out


# ---------------------------------------------------------------------------
# preview
# ---------------------------------------------------------------------------

def _count(sql: str, args=()) -> int:
    row = db.q1(sql, args)
    return int(next(iter(row.values())) if row else 0)


def _groups_referencing(source_ids: set[int]) -> list[int]:
    affected = []
    for row in db.q("SELECT id, source_ids_json FROM multiview_groups ORDER BY id"):
        members = set(_referenced_ids({"ids": db.jload(row["source_ids_json"], [])}, "ids"))
        if members & source_ids:
            affected.append(row["id"])
    return affected


def _live_workers(source_ids: set[int]) -> list[dict]:
    """Registered workers whose job covers a camera being removed."""
    live = []
    for job in db.q("SELECT id, name, source_ids FROM jobs ORDER BY id"):
        covered = set(_referenced_ids({"ids": db.jload(job["source_ids"], [])}, "ids"))
        if not covered & source_ids:
            continue
        for worker in db.q("SELECT * FROM worker_instances WHERE job_id=? ORDER BY id",
                           (job["id"],)):
            if worker["status"] in LIVE_WORKER_STATUSES:
                live.append({"worker_id": worker["id"], "worker_key": worker["worker_id"],
                             "name": worker["name"], "job_id": job["id"],
                             "job_name": job["name"], "status": worker["status"]})
    return live


def impact() -> dict:
    """What a reset would remove. Reads only; nothing is mutated."""
    source_ids = {row["id"] for row in db.q("SELECT id FROM sources")}
    group_ids = _groups_referencing(source_ids)
    groups = set(group_ids)
    group_clause = ",".join(str(value) for value in group_ids) or "NULL"
    return {
        "cameras": len(source_ids),
        "source_ids": sorted(source_ids),
        "stored_credentials": _count("SELECT COUNT(*) n FROM source_credentials"),
        "placements": _count("SELECT COUNT(*) n FROM sources WHERE map_x IS NOT NULL"),
        "calibrations": _count("SELECT COUNT(*) n FROM sources WHERE calibration_json IS NOT NULL"),
        "imported_calibrations": _count("SELECT COUNT(*) n FROM camera_calibrations"),
        "projection_surfaces": _count("SELECT COUNT(*) n FROM projection_surfaces"),
        "zone_views": _count("SELECT COUNT(*) n FROM zone_views"),
        "zone_geometry_provenance": _count(
            "SELECT COUNT(*) n FROM zone_geometry_provenance WHERE source_id IS NOT NULL"),
        "observations": _count("SELECT COUNT(*) n FROM events WHERE source_id IS NOT NULL"),
        "current_samples": _count("SELECT COUNT(*) n FROM source_current_samples"),
        "current_entities": _count("SELECT COUNT(*) n FROM source_current_entities"),
        "multiview_groups": len(group_ids),
        "multiview_group_ids": group_ids,
        "fused_entities": _count(
            f"SELECT COUNT(*) n FROM fused_entities WHERE group_id IN ({group_clause})"),
        "fused_observations": _count(
            f"SELECT COUNT(*) n FROM fused_observations WHERE group_id IN ({group_clause})"),
        "zone_occupancy_rows": _count(
            f"SELECT COUNT(*) n FROM zone_occupancy_observations WHERE group_id IN ({group_clause})"),
        "workers_to_stop": _live_workers(source_ids),
        "alert_rules_to_disable": affected_alert_rules(source_ids, groups),
        "saved_queries_becoming_stale": affected_saved_queries(source_ids, groups),
        "preserved": {
            "floor_plan": True,
            "space_dimensions": True,
            "canonical_zones": _count("SELECT COUNT(*) n FROM zones"),
            "saved_queries": _count("SELECT COUNT(*) n FROM analyses"),
            "dashboards": _count("SELECT COUNT(*) n FROM dashboards"),
            "alert_rule_definitions": _count("SELECT COUNT(*) n FROM alert_rules"),
        },
        "reset_token": reset_token(sorted(source_ids)),
    }


# ---------------------------------------------------------------------------
# execution
# ---------------------------------------------------------------------------

def execute() -> dict:
    """Remove every camera and its dependent state in one transaction."""
    removed = impact()
    source_ids = set(removed["source_ids"])
    group_ids = set(removed["multiview_group_ids"])
    if not source_ids:
        # Idempotent: a workspace with no cameras is already in the target state.
        return _result(removed, disabled=[], stale_queries=[], unbound=[],
                       stop_requested=[], already_empty=True)

    connection = db.connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        # Ask any live worker to stop through the lifecycle it already polls.
        # ManySight cannot terminate a process it never started, so the worker
        # rows stay readable until the process heartbeats and sees should_stop.
        for worker in removed["workers_to_stop"]:
            connection.execute(
                "UPDATE worker_instances SET desired_state='stopped', updated_at=? WHERE id=?",
                (db.now(), worker["worker_id"]))
        # Unbind jobs from cameras that will not exist, so nothing keeps a
        # dangling source id.
        unbound = []
        for job in connection.execute("SELECT id, name, source_ids FROM jobs").fetchall():
            covered = _referenced_ids({"ids": db.jload(job["source_ids"], [])}, "ids")
            remaining = [value for value in covered if value not in source_ids]
            if remaining != covered:
                connection.execute("UPDATE jobs SET source_ids=? WHERE id=?",
                                   (json.dumps(remaining), job["id"]))
                unbound.append({"job_id": job["id"], "job_name": job["name"],
                                "removed_source_ids": sorted(set(covered) - set(remaining))})

        group_clause = ",".join(str(value) for value in sorted(group_ids)) or "NULL"
        fused_ids = [row["id"] for row in connection.execute(
            f"SELECT id FROM fused_entities WHERE group_id IN ({group_clause})").fetchall()]
        if fused_ids:
            connection.execute(
                "DELETE FROM fused_entity_members WHERE fused_entity_id IN "
                f"({','.join('?' for _ in fused_ids)})", tuple(fused_ids))
        for table in ("fused_current_entities", "fused_observations", "fused_entities",
                      "zone_current_occupancy", "zone_occupancy_observations"):
            connection.execute(f"DELETE FROM {table} WHERE group_id IN ({group_clause})")
        connection.execute(f"DELETE FROM multiview_groups WHERE id IN ({group_clause})")

        for table in SOURCE_OWNED_TABLES:
            connection.execute(f"DELETE FROM {table}")
        # Provenance records how a zone was built from a camera. The camera is
        # going, and a row pointing at a deleted source id is exactly the
        # dangling reference this operation must not leave behind. The zone keeps
        # its geometry; only the camera-derived audit trail goes.
        connection.execute("DELETE FROM zone_geometry_provenance WHERE source_id IS NOT NULL")
        connection.execute("DELETE FROM events WHERE source_id IS NOT NULL")
        connection.execute("DELETE FROM sources")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
        # Sources, calibration and group membership all changed on a raw
        # connection, so the configuration cache and the live coordinator have
        # to be told; otherwise both keep serving the deleted workspace.
        from . import config_cache, realtime
        config_cache.invalidate("reset_cameras")
        realtime.coordinator.reset()

    disabled = _disable_rules(removed["alert_rules_to_disable"])
    return _result(removed, disabled=disabled,
                   stale_queries=removed["saved_queries_becoming_stale"], unbound=unbound,
                   stop_requested=removed["workers_to_stop"])


def _disable_rules(rules: list[dict]) -> list[dict]:
    """Disable rules the reset made inert. Definitions are preserved for repair."""
    for rule in rules:
        db.ex("UPDATE alert_rules SET enabled=0 WHERE id=?", (rule["id"],))
    return rules


def _result(removed: dict, disabled: list[dict], stale_queries: list[dict],
            unbound: list[dict], stop_requested: list[dict],
            already_empty: bool = False) -> dict:
    return {
        "reset": True,
        "dry_run": False,
        "already_empty": already_empty,
        "removed": {key: removed[key] for key in (
            "cameras", "source_ids", "stored_credentials", "placements", "calibrations",
            "imported_calibrations", "projection_surfaces", "zone_views",
            "zone_geometry_provenance", "observations", "current_samples", "current_entities",
            "multiview_groups", "multiview_group_ids", "fused_entities", "fused_observations",
            "zone_occupancy_rows")},
        "preserved": removed["preserved"],
        "alert_rules_disabled": disabled,
        "saved_queries_now_stale": stale_queries,
        "jobs_unbound": unbound,
        "workers_stop_requested": stop_requested,
        "workers_note": (
            "ManySight asked these workers to stop through their heartbeat. It cannot terminate "
            "a process it never started, so stop any that do not exit on their own."
            if stop_requested else None),
        "reset_token": reset_token([]),
    }
