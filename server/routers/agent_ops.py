"""Curated agent-facing surface: one snapshot, one capability check, one recipe,
one safe zone workflow, one workflow index.

Why these live here and not in the MCP adapter: the MCP server is a thin REST
client with no business logic, so every semantic operation an agent gets must be
a real platform endpoint that the dashboard, the SDK, a curl, and the test suite
can all reach. The endpoints below add no new derivation — they read the same
materialized state models and call the same routers the UI uses, and shape the
result for a caller whose context window is the scarce resource.

Nothing here returns credentials. Connection material stays behind the
separately authenticated /sources/{id}/connection endpoint.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from .. import db
from ..services import agent_workflows, current_state, homography, zone_geometry
from . import analytics_query, geometry as geometry_router, jobs, zones as zones_router

router = APIRouter(tags=["agent"])

# A source is "fresh" for capability purposes when its last complete sample is
# newer than this. It matches jobs.HEARTBEAT_STALE_S so a worker that stopped
# heartbeating and a source that stopped producing report staleness together.
SAMPLE_FRESH_S = 30.0
RATE_WINDOW_S = 30.0
# Enough to characterise a workspace without turning a snapshot into a data dump.
MAX_LISTED = 50


# ---------------------------------------------------------------------------
# shared readers
# ---------------------------------------------------------------------------

def _store() -> dict:
    return db.q1("SELECT * FROM stores WHERE id=1")


def _map_ready(store: dict) -> bool:
    plan = db.jload(store["map_json"], {})
    return bool(plan.get("walls"))


def _source_rows() -> list[dict]:
    return db.q("SELECT * FROM sources ORDER BY id")


def _configured(row: dict) -> bool:
    """Whether a worker could resolve access at all, without revealing how."""
    management = row.get("connection_management") or "external_secret"
    if management == "storelens_managed":
        return bool(db.jload(row.get("connection_config_json"), {}))
    return bool(db.jload(row.get("locator_json"), {}))


def _credential_status(source_id: int) -> str:
    stored = db.q1("SELECT credential_type FROM source_credentials WHERE source_id=?", (source_id,))
    return "stored" if stored else "absent"


def _calibrated(row: dict) -> bool:
    calibration = db.jload(row.get("calibration_json"), None)
    return bool(calibration and calibration.get("H"))


def _frame_size(source_id: int, row: dict) -> dict | None:
    rich = db.q1("SELECT frame_w,frame_h FROM camera_calibrations WHERE source_id=?", (source_id,))
    calibration = db.jload(row.get("calibration_json"), None) or {}
    width = (rich or {}).get("frame_w") or calibration.get("frame_w")
    height = (rich or {}).get("frame_h") or calibration.get("frame_h")
    return {"width": width, "height": height} if width and height else None


def _freshness(source_id: int, entity_type: str, now: float) -> dict:
    """Per-source complete-sample state, read from the materialized model."""
    sample = db.q1(
        "SELECT * FROM source_current_samples WHERE source_id=? AND entity_type=?",
        (source_id, entity_type),
    )
    if not sample:
        return {"has_complete_sample": False, "state": "unavailable", "last_sample_at": None,
                "age_s": None, "last_detection_count": None}
    age = max(0.0, now - sample["ts"])
    return {
        "has_complete_sample": True,
        "state": "healthy" if age <= SAMPLE_FRESH_S else "stale",
        "last_sample_at": sample["ts"],
        "age_s": round(age, 3),
        "last_detection_count": sample["expected_count"],
    }


def _submission_hz(source_id: int, entity_type: str, now: float) -> float | None:
    """Observed central submission rate: completion markers per second.

    Uses idx_events_source_name over a short window, so this stays cheap on a
    workspace with millions of rows. Returns None when there is nothing recent
    to measure rather than reporting a confident zero.
    """
    row = db.q1(
        "SELECT COUNT(*) n, MIN(ts) lo, MAX(ts) hi FROM events "
        "WHERE source_id=? AND name=? AND event_type='measurement' AND label=? AND ts>=?",
        (source_id, current_state.FRAME_COUNT_NAME, entity_type, now - RATE_WINDOW_S),
    )
    count = int((row or {}).get("n") or 0)
    if count < 2:
        return None
    span = float(row["hi"]) - float(row["lo"])
    return round((count - 1) / span, 2) if span > 0 else None


def _latest_worker(source_id: int) -> dict | None:
    """The most recent worker instance of the most recent job covering a source."""
    for job in db.q("SELECT id,name,source_ids,status FROM jobs ORDER BY created_at DESC, id DESC"):
        try:
            covered = [int(value) for value in db.jload(job["source_ids"], [])]
        except (TypeError, ValueError):
            continue
        if source_id not in covered:
            continue
        worker = db.q1(
            "SELECT * FROM worker_instances WHERE job_id=? ORDER BY created_at DESC LIMIT 1",
            (job["id"],),
        )
        if worker:
            return {"job_id": job["id"], "job_name": job["name"], "job_status": job["status"],
                    **jobs.serialize_worker(worker)}
    return None


def _sample_geometry(source_id: int, entity_type: str) -> dict:
    """What spatial evidence the current sample actually carries.

    A complete sample with no detections is a valid observed zero, not a missing
    capability, so tracking/geometry facts are `None` (not demonstrated by this
    sample) rather than False when there is nothing in frame.
    """
    rows = db.q(
        "SELECT e.track_id,e.bbox_json,e.x_px,e.x_map,e.identity_scope FROM source_current_entities c "
        "JOIN events e ON e.id=c.event_id WHERE c.source_id=? AND c.entity_type=? LIMIT 25",
        (source_id, entity_type),
    )
    if not rows:
        return {"tracking": None, "bbox_px": None, "point_px": None, "point_map": None,
                "identity_scopes": [], "observed_entities": 0, "current_sample_empty": True}
    return {
        "tracking": all(row["track_id"] for row in rows),
        "bbox_px": any(row["bbox_json"] for row in rows),
        "point_px": any(row["x_px"] is not None for row in rows),
        "point_map": any(row["x_map"] is not None for row in rows),
        "identity_scopes": sorted({row["identity_scope"] for row in rows if row["identity_scope"]}),
        "observed_entities": len(rows),
        "current_sample_empty": False,
    }


def _group_readiness(entity_type: str, now: float) -> list[dict]:
    """Multiview readiness per group, using the same freshness rule as fusion."""
    out = []
    for group in db.q("SELECT * FROM multiview_groups ORDER BY id"):
        source_ids = [int(value) for value in db.jload(group["source_ids_json"], [])]
        calibrated, fresh, stale = [], [], []
        for source_id in source_ids:
            row = db.q1("SELECT calibration_json FROM sources WHERE id=?", (source_id,))
            if row and _calibrated(row):
                calibrated.append(source_id)
            sample = db.q1(
                "SELECT ts FROM source_current_samples WHERE source_id=? AND entity_type=?",
                (source_id, entity_type),
            )
            if sample and now - sample["ts"] <= group["track_age_s"]:
                fresh.append(source_id)
            else:
                stale.append(source_id)
        fused = db.q1(
            "SELECT COUNT(*) n FROM fused_current_entities WHERE group_id=? AND entity_type=?",
            (group["id"], entity_type),
        )
        out.append({
            "id": group["id"], "name": group["name"], "enabled": bool(group["enabled"]),
            "source_ids": source_ids,
            "calibrated_source_ids": calibrated,
            "fresh_source_ids": fresh, "stale_source_ids": stale,
            "quality": ("known" if fresh and len(fresh) == len(source_ids)
                        else "partial" if fresh else "unknown"),
            "fused_entity_count": int((fused or {}).get("n") or 0),
            "gates": {"time_tolerance_s": group["time_tolerance_s"],
                      "spatial_gate_m": group["spatial_gate_m"],
                      "track_age_s": group["track_age_s"]},
            "configuration_revision": group["configuration_revision"],
        })
    return out


def _zone_summary() -> list[dict]:
    views = db.q("SELECT zone_id,source_id FROM zone_views ORDER BY id")
    by_zone: dict[int, list[int]] = {}
    for view in views:
        by_zone.setdefault(view["zone_id"], []).append(view["source_id"])
    out = []
    for zone in db.q("SELECT * FROM zones ORDER BY id LIMIT ?", (MAX_LISTED,)):
        shape = db.jload(zone.get("geometry_json"), None) or {}
        out.append({
            "id": zone["id"], "name": zone["name"], "ztype": zone["ztype"],
            "revision": zone["revision"],
            "geometry_type": shape.get("type"),
            "component_count": zone_geometry.component_count(shape) if shape else 0,
            "zone_view_source_ids": sorted(by_zone.get(zone["id"], [])),
        })
    return out


def _analytics_summary() -> dict:
    queries = db.q(
        "SELECT id,name,subject,measures_json,filters_json,status FROM analyses "
        "WHERE visibility='visible' ORDER BY id LIMIT ?", (MAX_LISTED,))
    rules = db.q("SELECT * FROM alert_rules ORDER BY id LIMIT ?", (MAX_LISTED,))
    dashboards = db.q("SELECT id,name FROM dashboards ORDER BY id LIMIT ?", (MAX_LISTED,))
    widget_counts = {row["dashboard_id"]: row["n"] for row in db.q(
        "SELECT dashboard_id, COUNT(*) n FROM dashboard_widgets GROUP BY dashboard_id")}
    return {
        "saved_queries": [{
            "id": row["id"], "name": row["name"], "subject": row["subject"],
            "measures": db.jload(row["measures_json"], []),
            "filters": db.jload(row["filters_json"], {}),
            "status": row["status"],
        } for row in queries],
        "dashboards": [{"id": row["id"], "name": row["name"],
                        "widget_count": widget_counts.get(row["id"], 0)} for row in dashboards],
        "alert_rules": [{
            "id": row["id"], "name": row["name"], "kind": row["kind"],
            "enabled": bool(row["enabled"]),
            "params": db.jload(row["params_json"], {}),
            "condition": db.jload(row.get("condition_json"), None),
            "last_fired_at": row["last_fired_at"],
        } for row in rules],
    }


def _query_capabilities() -> dict:
    """A trimmed capability block: enough to compose a valid query, not a dump."""
    full = analytics_query.capabilities()
    return {
        "subjects": full["subjects"],
        "measures_by_subject": full["measures_by_subject"],
        "groupings": full["groupings"],
        "split_dimensions": full["split_dimensions"],
        "present_entity_types": full["entity_types"][:MAX_LISTED],
        "present_measurement_names": full["measurement_names"][:MAX_LISTED],
        "present_state_names": full["state_names"][:MAX_LISTED],
        "present_labels": full["labels"][:MAX_LISTED],
    }


# ---------------------------------------------------------------------------
# inspect_workspace
# ---------------------------------------------------------------------------

@router.get("/agent/workspace", summary="One-call workspace readiness snapshot for an agent")
def inspect_workspace(entity_type: str = "person"):
    """The natural first call for an agent task.

    Reconstructing this from the low-level API costs eight or more round trips;
    every field here comes from a materialized read model or a bounded query.
    """
    now = db.now()
    store = _store()
    revision = db.q1("SELECT id,revision_number,status FROM space_revisions WHERE id=?",
                     (db.current_space_revision_id(),))
    surfaces = {row["source_id"]: row["n"] for row in db.q(
        "SELECT source_id, COUNT(*) n FROM projection_surfaces GROUP BY source_id")}
    view_counts = {row["source_id"]: row["n"] for row in db.q(
        "SELECT source_id, COUNT(*) n FROM zone_views GROUP BY source_id")}

    sources = []
    for row in _source_rows():
        freshness = _freshness(row["id"], entity_type, now)
        sources.append({
            "id": row["id"], "name": row["name"], "kind": row["kind"],
            "configured": _configured(row),
            "connection_management": row.get("connection_management") or "external_secret",
            "credential_status": _credential_status(row["id"]),
            "placed": row["map_x"] is not None,
            "calibrated": _calibrated(row),
            "calibration_revision": row.get("calibration_revision") or 0,
            "frame_size": _frame_size(row["id"], row),
            "projection_surface_count": surfaces.get(row["id"], 0),
            "zone_view_count": view_counts.get(row["id"], 0),
            "observation_state": freshness["state"],
            "last_sample_age_s": freshness["age_s"],
        })

    calibrated_ids = [item["id"] for item in sources if item["calibrated"]]
    uncalibrated_ids = [item["id"] for item in sources if not item["calibrated"]]
    zones = _zone_summary()
    groups = _group_readiness(entity_type, now)
    fresh_source_ids = [item["id"] for item in sources if item["observation_state"] == "healthy"]
    entity_types = [row["entity_type"] for row in db.q(
        "SELECT DISTINCT entity_type FROM source_current_samples ORDER BY entity_type")]

    readiness = {
        "map": "ready" if _map_ready(store) else "missing",
        "calibration": ("ready" if sources and not uncalibrated_ids
                        else "partial" if calibrated_ids else "missing"),
        "zones": "ready" if zones else "missing",
        "perception": ("ready" if fresh_source_ids and len(fresh_source_ids) == len(sources)
                       else "partial" if fresh_source_ids else "missing"),
        "multiview": ("ready" if any(group["quality"] == "known" for group in groups)
                      else "partial" if groups else "missing"),
    }
    next_steps = []
    if readiness["map"] != "ready":
        next_steps.append("No metric plan yet — digitize or configure the space before geometry work.")
    if uncalibrated_ids:
        next_steps.append(
            f"Sources {uncalibrated_ids} are not calibrated; they cannot contribute geometry or fusion.")
    if not zones:
        next_steps.append(
            "No canonical zones. For a named region, follow the define-zone-from-cameras workflow "
            "instead of asking the user for coordinates.")
    if readiness["perception"] != "ready":
        next_steps.append(
            f"Call inspect_perception before assuming '{entity_type}' data exists; a missing "
            "complete sample means unknown, not zero.")

    return {
        "workspace": {
            "name": store["name"], "space_type": store.get("space_type") or "store",
            "environment": store.get("environment") or "setup",
            "width_m": store["width_m"], "height_m": store["height_m"],
            "map_ready": _map_ready(store),
            "space_revision_id": (revision or {}).get("id"),
            "space_revision_number": (revision or {}).get("revision_number"),
            "as_of": now,
        },
        "sources": sources,
        "geometry": {
            "map_ready": _map_ready(store),
            "calibrated_source_ids": calibrated_ids,
            "uncalibrated_source_ids": uncalibrated_ids,
            "projection_surface_count": sum(surfaces.values()),
            "zones": zones,
        },
        "perception": {
            "entity_type": entity_type,
            "observed_entity_types": entity_types,
            "sources_with_complete_samples": [
                item["id"] for item in sources if item["observation_state"] != "unavailable"],
            "fresh_source_ids": fresh_source_ids,
            "sample_fresh_after_s": SAMPLE_FRESH_S,
        },
        "multiview": {"groups": groups},
        "analytics": {**_analytics_summary(), "query_capabilities": _query_capabilities()},
        "readiness": readiness,
        "next_steps": next_steps,
    }


# ---------------------------------------------------------------------------
# inspect_source / frame capture plan
# ---------------------------------------------------------------------------

@router.get("/agent/sources/{source_id}", summary="Everything an agent needs about one source")
def inspect_source(source_id: int, entity_type: str = "person"):
    row = db.q1("SELECT * FROM sources WHERE id=?", (source_id,))
    if not row:
        raise HTTPException(404, "source not found")
    now = db.now()
    rich = db.q1("SELECT provider,units,ground_plane_z,revision,world_frame_json,verification_json "
                 "FROM camera_calibrations WHERE source_id=?", (source_id,))
    views = db.q("SELECT id,zone_id,revision,membership_rule,projection_surface_id FROM zone_views "
                 "WHERE source_id=? ORDER BY id", (source_id,))
    zone_names = {z["id"]: z["name"] for z in db.q("SELECT id,name FROM zones")}
    return {
        "id": row["id"], "name": row["name"], "kind": row["kind"],
        "connection": {
            "configured": _configured(row),
            "management": row.get("connection_management") or "external_secret",
            "mode": row.get("connection_mode") or "agent_local",
            "revision": int(row.get("connection_revision") or 0),
            "credential_status": _credential_status(source_id),
            "resolution": "Call get_source_connection only inside an authorized local worker.",
        },
        "capabilities": db.jload(row.get("capabilities_json"), []),
        "placement": ({"x": row["map_x"], "y": row["map_y"], "rotation_deg": row["rotation_deg"],
                       "fov_deg": row["fov_deg"]} if row["map_x"] is not None else None),
        "calibration": {
            "floor_homography": _calibrated(row),
            "revision": row.get("calibration_revision") or 0,
            "frame_size": _frame_size(source_id, row),
            "rich_import": ({"provider": rich["provider"], "units": rich["units"],
                             "ground_plane_z": rich["ground_plane_z"], "revision": rich["revision"],
                             "world_frame": db.jload(rich["world_frame_json"], {})} if rich else None),
        },
        "geometry": {
            "projection_surfaces": db.q(
                "SELECT id,name,kind,height_m,revision FROM projection_surfaces WHERE source_id=? ORDER BY id",
                (source_id,)),
            "zone_views": [{**view, "zone_name": zone_names.get(view["zone_id"])} for view in views],
        },
        "perception": {
            "entity_type": entity_type,
            **_freshness(source_id, entity_type, now),
            "submission_hz": _submission_hz(source_id, entity_type, now),
            "evidence": _sample_geometry(source_id, entity_type),
            "worker": _latest_worker(source_id),
        },
        "visual_inspection": "GET /api/v1/agent/sources/{id}/frame-capture-plan",
    }


@router.get("/agent/sources/{source_id}/frame-capture-plan",
            summary="How to capture one frame from this source locally")
def frame_capture_plan(source_id: int):
    """A runnable local plan, never image bytes.

    StoreLens does not proxy media and the MCP adapter does not process video, so
    a frame is captured by the caller's own process. This endpoint supplies the
    exact, current way to do that, including the geometry context needed to turn
    the resulting image into zone polygons.
    """
    row = db.q1("SELECT * FROM sources WHERE id=?", (source_id,))
    if not row:
        raise HTTPException(404, "source not found")
    management = row.get("connection_management") or "external_secret"
    frame_size = _frame_size(source_id, row)
    return {
        "source_id": source_id, "name": row["name"], "kind": row["kind"],
        "executed_by": "caller",
        "why": ("StoreLens never opens or proxies a source feed, so no API returns live camera "
                "pixels. Run this plan in your own shell and open the saved image."),
        "prerequisites": [
            "The camera must be reachable from the machine running the plan.",
            "STORELENS_CREDENTIAL_ACCESS_KEY must be set for a storelens_managed source."
            if management == "storelens_managed"
            else "Resolve locator.local_secret_ref from your own environment or keychain.",
        ],
        "plan": [
            "import sys; sys.path.insert(0, 'sdk/python')",
            "import storelens as sl",
            f"client = sl.StoreLens('<rest base>', api_key='<key or empty>')",
            f"source = client.source({source_id})",
            "cap = client.open_capture(source)   # resolves access in memory only",
            "ok, frame = cap.read(); cap.release()",
            "import cv2; cv2.imwrite('frame.jpg', frame)   # then look at frame.jpg",
        ],
        "sdk": {"module": "sdk/python/storelens.py", "helper": "StoreLens.open_capture(source)"},
        "geometry_context": {
            "calibrated": _calibrated(row),
            "frame_size": frame_size,
            "pixel_coordinate_system": (
                "Top-left origin, x right, y down, in the source's own frame size. Zone-view "
                "polygons and preview_zone use exactly these coordinates."),
            "projection": ("Pixels project to map metres through the floor homography; pass a "
                           "projection_surface_id for a non-floor plane."),
        },
        "safety": [
            "Never log, print, or persist the resolved connection.",
            "Never write the captured frame into observations, zone metadata, or job metadata.",
        ],
    }


# ---------------------------------------------------------------------------
# inspect_perception
# ---------------------------------------------------------------------------

@router.get("/agent/perception", summary="Does the perception this task needs already exist?")
def inspect_perception(
    entity_type: str = "person",
    source_ids: Annotated[str | None,
                          Query(description="Comma-separated source IDs; omit for all")] = None,
    require_tracking: bool = True,
    require_spatial: bool = True,
):
    """Answer 'can StoreLens already answer this?' before starting any worker.

    Derived entirely from existing job/worker/observation records — there is no
    separate capability registry to drift out of sync with reality.
    """
    now = db.now()
    if source_ids:
        try:
            wanted = [int(value) for value in source_ids.split(",") if value.strip()]
        except ValueError as exc:
            raise HTTPException(422, "source_ids must be comma-separated integers") from exc
    else:
        wanted = [row["id"] for row in _source_rows()]
    known = {row["id"]: row for row in _source_rows()}
    missing = [source_id for source_id in wanted if source_id not in known]
    if missing:
        raise HTTPException(404, f"unknown source ids: {missing}")

    per_source, workers = [], {}
    for source_id in wanted:
        row = known[source_id]
        freshness = _freshness(source_id, entity_type, now)
        evidence = _sample_geometry(source_id, entity_type)
        worker = _latest_worker(source_id)
        if worker:
            workers[worker["job_id"]] = worker
        metrics = (worker or {}).get("metrics") or {}
        per_source.append({
            "source_id": source_id, "name": row["name"],
            "state": freshness["state"],
            "available": freshness["has_complete_sample"],
            "calibrated": _calibrated(row),
            "last_sample_at": freshness["last_sample_at"],
            "age_s": freshness["age_s"],
            "last_detection_count": freshness["last_detection_count"],
            "submission_hz": _submission_hz(source_id, entity_type, now),
            # Local decode/inference rate is the worker's own business; StoreLens
            # only knows it if the worker reports it in heartbeat metrics.
            "local_fps": metrics.get("local_fps") or metrics.get("fps"),
            "tracking": evidence["tracking"],
            "current_sample_empty": evidence["current_sample_empty"],
            "output": {"bbox_px": evidence["bbox_px"], "point_px": evidence["point_px"],
                       "point_map": evidence["point_map"]},
            "identity_scopes": evidence["identity_scopes"],
            "worker": ({"job_id": worker["job_id"], "job_name": worker["job_name"],
                        "worker_id": worker["worker_id"],
                        "effective_status": worker["effective_status"],
                        "desired_state": worker["desired_state"],
                        "last_heartbeat_at": worker["last_heartbeat_at"],
                        "last_error": worker["last_error"]} if worker else None),
        })

    def _satisfies(item: dict) -> bool:
        # `None` means the current sample is a complete observed zero, which
        # neither proves nor disproves the capability — it must not disqualify a
        # fresh source, or a camera correctly reporting an empty aisle would look
        # broken.
        if require_tracking and item["tracking"] is False:
            return False
        spatial = [item["output"][key] for key in ("bbox_px", "point_px", "point_map")]
        if require_spatial and not any(value is None or value for value in spatial):
            return False
        return item["state"] == "healthy"

    healthy = [item for item in per_source if _satisfies(item)]
    healthy_ids = [item["source_id"] for item in healthy]
    stale_ids = [item["source_id"] for item in per_source if item["state"] == "stale"]
    unavailable_ids = [item["source_id"] for item in per_source if item["state"] == "unavailable"]

    if healthy_ids and len(healthy_ids) == len(wanted):
        capability_state, action = "healthy", "reuse"
    elif healthy_ids:
        capability_state, action = "partial", "extend_coverage"
    elif stale_ids:
        capability_state, action = "stale", "restart_or_repair"
    else:
        capability_state, action = "unavailable", "perception_missing"

    groups = _group_readiness(entity_type, now)
    covering = [group for group in groups
                if set(wanted) & set(group["source_ids"]) and group["enabled"]]
    reasons = []
    if unavailable_ids:
        reasons.append(f"No complete {entity_type} sample has ever arrived for {unavailable_ids}.")
    if stale_ids:
        reasons.append(
            f"Sources {stale_ids} have a last complete sample older than {SAMPLE_FRESH_S:.0f}s — "
            "treat their contribution as unknown, never as zero.")
    if require_tracking and any(item["tracking"] is False for item in per_source):
        reasons.append("Detections arrive without a source-local entity_id, so tracking is unavailable.")
    empty = [item["source_id"] for item in per_source
             if item["state"] == "healthy" and item["current_sample_empty"]]
    if empty:
        reasons.append(
            f"Sources {empty} are fresh and report a complete empty sample — that is an "
            f"observed zero for '{entity_type}', not missing perception.")
    if not covering and len(wanted) > 1:
        reasons.append(
            "These sources are not in one enabled multiview group, so cross-camera counting would "
            "double-count. Use configure-multiview before occupancy questions.")

    return {
        "request": {"entity_type": entity_type, "source_ids": wanted,
                    "require_tracking": require_tracking, "require_spatial": require_spatial},
        "capability": {
            "entity_type": entity_type, "task": "detection_tracking",
            "state": capability_state, "action": action,
            "healthy_source_ids": healthy_ids, "stale_source_ids": stale_ids,
            "unavailable_source_ids": unavailable_ids,
            "sample_contract": "detection_sample",
            "complete_sample_semantics": "supported",
            "empty_frame_meaning": "detections=[] is an explicit observed zero",
            "no_fresh_sample_meaning": "unknown or stale, never zero",
        },
        "sources": per_source,
        "compatible_jobs": [{"job_id": worker["job_id"], "job_name": worker["job_name"],
                             "job_status": worker["job_status"],
                             "effective_status": worker["effective_status"]}
                            for worker in workers.values()],
        "multiview": {
            "groups": covering,
            "ready": bool(covering and all(group["quality"] == "known" for group in covering)),
        },
        "observed_entity_types": [row["entity_type"] for row in db.q(
            "SELECT DISTINCT entity_type FROM source_current_samples ORDER BY entity_type")],
        "reasons": reasons,
        "next": ("Reuse the existing perception; do not start another worker."
                 if action == "reuse" else
                 "Call get_worker_recipe for the current contract, then run a local worker."),
    }


# ---------------------------------------------------------------------------
# get_worker_recipe
# ---------------------------------------------------------------------------

@router.get("/agent/worker-recipe", summary="The current worker integration contract")
def worker_recipe(entity_type: str = "person", tracking: bool = True,
                  source_ids: str | None = None):
    """The authoritative answer to 'how do I submit perception to StoreLens now?'.

    Generated from the running platform, so it cannot fall behind the way a demo
    or example script in a repository can.
    """
    from .observations import DetectionSampleIn  # local import: heavy module

    wanted: list[int] = []
    if source_ids:
        try:
            wanted = [int(value) for value in source_ids.split(",") if value.strip()]
        except ValueError as exc:
            raise HTTPException(422, "source_ids must be comma-separated integers") from exc
    fields = sorted(DetectionSampleIn.model_fields)
    return {
        "authority": (
            "This recipe plus GET /observations/contract and /openapi.json are the current "
            "contract. Do NOT infer it from an example, demo, or older worker script found in a "
            "repository — those may predate the current API."),
        "entity_type": entity_type, "tracking": tracking, "source_ids": wanted,
        "submission": {
            "preferred_endpoint": "POST /api/v1/detection-samples",
            "envelope": "one successfully processed source frame",
            "envelope_fields": fields,
            "atomic": True,
            "empty_frame": "detections=[] — a real observed zero that must be submitted",
            "never": "a fake or zero-confidence detection standing in for an empty frame",
            "idempotency": ("Re-posting an identical sample_id with identical contents is a "
                            "duplicate no-op; reusing it with different contents is a 409."),
            "legacy": {
                "endpoint": "POST /api/v1/observations/batch",
                "marker": f"{current_state.FRAME_COUNT_NAME} measurement per frame",
                "use_when": "Only for non-detection kinds or an existing legacy worker. Prefer "
                            "detection-samples for new detection work.",
            },
        },
        "identity": {
            "entity_id": "opaque source-local tracker ID, never a verified identity",
            "identity_scope": ["worker_run", "source", "workspace"],
            "cross_camera": ("Never join IDs across sources. Cross-camera association is "
                             "StoreLens multiview fusion and stays anonymous."),
        },
        "spatial": {
            "preferred": "bbox_px corner form plus point_px",
            "floor_point": "feet or bbox bottom-centre for floor traffic",
            "resolution": ("Pixels in the source's own frame size. StoreLens projects them; "
                           "workers never send map coordinates from a camera, zone_id, or zone."),
        },
        "forbidden_worker_output": sorted(
            ["zone_id", "zone", "zone_enter", "zone_exit", "zone_dwell", "state_change", "count",
             "dwell", "occupancy", "visits", "transitions", "fused identity"]),
        "sampling": {
            "principle": ("Local detection and tracking may run at full camera FPS. The central "
                          "submission rate is a separate, task-driven choice — submitting every "
                          "decoded frame is usually unnecessary."),
            "guidance": [
                "Current occupancy and zone presence: a few Hz is normally sufficient.",
                "Dwell and visit boundaries: raise the rate until visit edges are stable.",
                "Fast movement or tight spatial gates: match the multiview time tolerance.",
                "There is no globally correct rate; state the rate you chose and why.",
            ],
            "report_in_heartbeat": ["local_fps", "submission_hz"],
        },
        "lifecycle": {
            "register_job": "POST /api/v1/jobs before submitting",
            "register_worker": "POST /api/v1/workers for the process you actually started",
            "heartbeat": "POST /api/v1/workers/{id}/heartbeat every 5-15s",
            "obey": ("The heartbeat response carries should_stop and restart_requested; exit "
                     "cleanly when asked. StoreLens never launches or relaunches your process."),
            "sdk": {"module": "sdk/python/storelens.py",
                    "sample_builder": "StoreLens.begin_detection_sample(...) / submit_detection_sample(...)",
                    "note": "Imported via sys.path; not an installed package."},
        },
        "source_access": {
            "owner": "the local worker",
            "resolve": "GET /api/v1/sources/{id}/connection with X-StoreLens-Credential-Key",
            "rules": ["in memory only", "never logged, persisted, or echoed into observations"],
        },
        "local_environment": [
            "Reuse an existing project virtualenv or conda environment before creating one.",
            "Verify CUDA/PyTorch and model weights in that environment rather than assuming.",
            "This is guidance for your own shell; StoreLens executes nothing on your behalf.",
        ],
        "multiview_prerequisites": [
            "Every fused source calibrated into the same metric world frame.",
            "Complete samples from each source inside the group's time tolerance.",
            "An explicit enabled multiview group.",
        ],
        "verify": [
            "GET /api/v1/agent/perception — worker heartbeat, freshness, submission rate.",
            "GET /api/v1/observations/latest-frames — the current complete sample per source.",
            "GET /api/v1/multiview/current — fused entities with member evidence.",
        ],
        "skill": "perception-workers",
        "contract_endpoint": "GET /api/v1/observations/contract",
    }


# ---------------------------------------------------------------------------
# zone preview / commit
# ---------------------------------------------------------------------------

class CameraPolygonIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: int
    polygon_px: list[dict]
    detection_polygon_px: list[dict] | None = None
    projection_surface_id: int | None = None
    membership_rule: str = "point"
    threshold: float = 0.5
    min_keypoints: int = 1


class ZonePreviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    zone_name: str = ""
    zone_id: int | None = None
    ztype: str = "area"
    views: list[CameraPolygonIn] = Field(default_factory=list)


class ZoneCommitIn(ZonePreviewIn):
    approved: bool = False


def _projection_matrix(view: CameraPolygonIn) -> tuple[list, dict | None, str, dict]:
    row = db.q1("SELECT calibration_json,calibration_revision FROM sources WHERE id=?",
                (view.source_id,))
    if not row:
        raise HTTPException(404, f"source {view.source_id} not found")
    if view.projection_surface_id is not None:
        surface = db.q1("SELECT * FROM projection_surfaces WHERE id=?", (view.projection_surface_id,))
        if not surface:
            raise HTTPException(404, f"projection surface {view.projection_surface_id} not found")
        if surface["source_id"] != view.source_id:
            raise HTTPException(422, "projection surface belongs to a different source")
        return db.jload(surface["homography_json"], None), surface, "projection_surface", row
    calibration = db.jload(row["calibration_json"], None)
    return (calibration or {}).get("H"), None, "floor_calibration", row


def _preview_views(body: ZonePreviewIn) -> tuple[list[dict], list[dict], list[str]]:
    """Project each proposed camera polygon without writing anything."""
    if not body.views:
        raise HTTPException(422, "at least one camera view polygon is required")
    projected, contributions, warnings = [], [], []
    for view in body.views:
        if len(view.polygon_px) < 3:
            raise HTTPException(422, f"source {view.source_id}: a polygon needs at least 3 points")
        matrix, surface, plane, source = _projection_matrix(view)
        item = {"source_id": view.source_id, "plane": plane,
                "projection_surface_id": view.projection_surface_id,
                "polygon_px": view.polygon_px, "membership_rule": view.membership_rule}
        if not matrix:
            item.update({"valid": False, "polygon_map": None,
                         "error": "source has no floor calibration or usable projection surface"})
            warnings.append(f"Source {view.source_id} is not calibrated; calibrate it before "
                            "contributing geometry.")
            projected.append(item)
            continue
        points = [{"x": float(x), "y": float(y)}
                  for x, y in homography.project(matrix, view.polygon_px)]
        try:
            shape = zone_geometry.polygon_from_points(points)
        except ValueError as exc:
            item.update({"valid": False, "polygon_map": points, "error": str(exc)})
            warnings.append(f"Source {view.source_id}: {exc}")
            projected.append(item)
            continue
        item.update({
            "valid": True,
            "polygon_map": [{"x": round(point["x"], 3), "y": round(point["y"], 3)}
                            for point in points],
            "area_m2": round(shape.area, 3),
            "calibration_revision": source["calibration_revision"],
            "projection_surface_revision": (surface or {}).get("revision"),
        })
        if shape.area <= 0.01:
            warnings.append(f"Source {view.source_id} projects to {shape.area:.3f} m2 — check the "
                            "polygon and the calibration.")
        contributions.append(points)
        projected.append(item)
    return projected, contributions, warnings


def _combined(body: ZonePreviewIn, contributions: list[list[dict]]) -> dict | None:
    """Union the projected contributions exactly the way a commit will."""
    existing = None
    if body.zone_id is not None:
        zone = db.q1("SELECT * FROM zones WHERE id=?", (body.zone_id,))
        if not zone:
            raise HTTPException(404, "zone not found")
        existing = db.jload(zone.get("geometry_json"), None)
    if not contributions and not existing:
        return None
    result = existing
    for points in contributions:
        if result is None:
            result = zone_geometry.as_geojson(zone_geometry.polygon_from_points(points))
        else:
            result = zone_geometry.as_geojson(zone_geometry.union(result, points))
    return result


@router.post("/agent/zone-preview", summary="Project proposed camera polygons without persisting")
def preview_zone(body: ZonePreviewIn):
    """Subjective geometry gets previewed and approved before it is stored.

    Nothing in this response has been written. Call it again after every user
    correction; call zone-commit only once the user approves.
    """
    projected, contributions, warnings = _preview_views(body)
    combined = _combined(body, contributions)
    shape = zone_geometry.normalize(zone_geometry.from_geojson(combined)) if combined else None
    return {
        "persisted": False,
        "zone_name": body.zone_name or (db.q1("SELECT name FROM zones WHERE id=?", (body.zone_id,))
                                        or {}).get("name"),
        "zone_id": body.zone_id,
        "views": projected,
        "canonical_preview": {
            "geometry": combined,
            "geometry_type": (combined or {}).get("type"),
            "component_count": zone_geometry.component_count(combined) if combined else 0,
            "area_m2": round(shape.area, 3) if shape is not None else None,
        },
        "provenance": {
            "contributing_source_ids": [item["source_id"] for item in projected if item["valid"]],
            "operation": ("extend existing canonical zone" if body.zone_id
                          else "create one canonical zone"),
            "note": ("Each contributing camera becomes one ZoneView; the canonical zone is their "
                     "union in map metres. Cameras that cannot see the region get no ZoneView."),
        },
        "warnings": warnings,
        "next": ("Show this preview to the user. Re-preview after any correction. Call "
                 "zone-commit with approved=true only after explicit approval."),
    }


@router.post("/agent/zone-commit", status_code=201,
             summary="Persist an approved zone: one canonical zone plus per-camera ZoneViews")
def commit_zone(body: ZoneCommitIn):
    """Runs exactly the validated low-level sequence: create or reuse the canonical
    zone from the first contribution, create one ZoneView per camera, then union
    each remaining contribution in with explicit `extend_zone_from_view` calls so
    every step records projection provenance.
    """
    if not body.approved:
        raise HTTPException(422, "commit requires approved=true after the user approved the preview")
    projected, _, warnings = _preview_views(body)
    invalid = [item["source_id"] for item in projected if not item["valid"]]
    if invalid:
        raise HTTPException(422, f"cannot commit: sources {invalid} produced no valid projection")
    if body.zone_id is None and not body.zone_name.strip():
        raise HTTPException(422, "zone_name is required when creating a canonical zone")

    views = list(body.views)
    created_view_ids, extensions = [], []
    zone_id = body.zone_id
    if zone_id is None:
        seed = views[0]
        zone = zones_router.create_zone(zones_router.ZoneIn(
            name=body.zone_name.strip(), ztype=body.ztype,
            polygon_px=seed.polygon_px, source_id=seed.source_id,
        ))
        zone_id = zone["id"]
        created_view_ids.append(_create_view(zone_id, seed)["id"])
        views = views[1:]
    for view in views:
        created = _create_view(zone_id, view)
        created_view_ids.append(created["id"])
        extended = geometry_router.extend_zone_from_view(
            created["id"], geometry_router.ZoneExtensionIn())
        extensions.append({"source_id": view.source_id, "zone_view_id": created["id"],
                           "projected_contribution_m": extended["projected_contribution"]})
    zone = zones_router.get_zone(zone_id)
    return {
        "persisted": True,
        "zone": {"id": zone["id"], "name": zone["name"], "ztype": zone["ztype"],
                 "revision": zone["revision"], "geometry": zone["geometry"],
                 "component_count": zone["component_count"]},
        "zone_view_ids": created_view_ids,
        "extensions": extensions,
        "geometry_provenance": zone["geometry_provenance"],
        "canonical": ("One physical region is one canonical zone. The ZoneViews above are "
                      "camera-specific pixel evidence, not separate zones."),
        "warnings": warnings,
    }


def _create_view(zone_id: int, view: CameraPolygonIn) -> dict:
    return geometry_router.create_zone_view(geometry_router.ZoneViewIn(
        zone_id=zone_id, source_id=view.source_id,
        outer_polygon_px=view.polygon_px,
        detection_polygon_px=view.detection_polygon_px or view.polygon_px,
        projection_surface_id=view.projection_surface_id,
        membership_rule=view.membership_rule, threshold=view.threshold,
        min_keypoints=view.min_keypoints,
    ))


# ---------------------------------------------------------------------------
# workflow discovery
# ---------------------------------------------------------------------------

@router.get("/agent/workflows", summary="Index of StoreLens agent workflows")
def list_workflows():
    return {"workflows": agent_workflows.index(),
            "skills_endpoint": "MCP get_skill(name) or skills/<name>/SKILL.md",
            "note": "Fetch one workflow for prerequisites, sequence, invariants, and tools."}


@router.get("/agent/workflows/{name}", summary="One workflow: prerequisites, sequence, invariants")
def get_workflow(name: str):
    item = agent_workflows.get(name)
    if not item:
        raise HTTPException(404, f"unknown workflow '{name}'; call GET /api/v1/agent/workflows")
    return item
