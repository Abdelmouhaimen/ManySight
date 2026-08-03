"""Shared enrichment pipeline: representative point -> projection surface selection
-> map projection -> zone-view matching -> canonical zone assignment -> geometry
revision persistence.

This is the ONE implementation of geometry enrichment in StoreLens. Both the legacy
`POST /events` endpoint and the `POST /observations/batch` endpoint (the current
worker contract: detection | measurement | state) call `enrich_one` so a change to
projection precedence, zone-view scoring, or revision bookkeeping only has to be
made here. Callers pass a plain dict of already-validated fields — this module has
no opinion on which pydantic model produced it.
"""
from .. import db
from . import homography

# Kinds a *worker* may submit under the current (schema_version=2) contract.
OBSERVATION_KINDS = {"detection", "measurement", "state"}
# Legacy event_types still accepted by /events for backward compatibility and still
# queryable for historical audit, but no longer part of the worker contract: workers
# must not calculate zone entry/exit, dwell, occupancy, transitions, or state changes
# -- StoreLens derives all of those from detection/state observations.
LEGACY_DERIVED_KINDS = {"zone_enter", "zone_exit", "zone_dwell", "state_change", "count"}
# Every event_type the `events` table has ever accepted (legacy + current), used by
# the legacy /events endpoint's own validation.
ALL_EVENT_TYPES = OBSERVATION_KINDS | LEGACY_DERIVED_KINDS | {"transition", "custom"}


def load_geometry_context():
    """Zones, floor calibrations, projection surfaces, and zone views as of now.
    Load once per ingest batch, not once per event."""
    zones = [{"id": z["id"], "name": z["name"], "revision": z["revision"],
              "polygon": db.jload(z["polygon_json"], [])}
             for z in db.q("SELECT id, name, polygon_json, revision FROM zones ORDER BY id")]
    cals = {}
    for s in db.q("SELECT id, calibration_json, calibration_revision FROM sources"):
        cal = db.jload(s["calibration_json"], None)
        if cal and cal.get("H"):
            cals[s["id"]] = {"H": cal["H"], "revision": s["calibration_revision"]}
    surfaces = {r["id"]: {**r, "H": db.jload(r["homography_json"], None)}
                for r in db.q("SELECT * FROM projection_surfaces")}
    views_by_source, views_by_id = {}, {}
    for r in db.q("SELECT * FROM zone_views ORDER BY id"):
        view = {**r, "outer": db.jload(r["outer_polygon_json"], []),
                "detection": db.jload(r["detection_polygon_json"], [])}
        views_by_source.setdefault(r["source_id"], []).append(view)
        views_by_id[r["id"]] = view
    zone_by_name = {z["name"].lower(): z["id"] for z in zones}
    return zones, cals, surfaces, views_by_source, views_by_id, zone_by_name


def view_score(view: dict, ev: dict, x_px: float | None, y_px: float | None) -> float:
    polygon = view["detection"] or view["outer"]
    rule = view["membership_rule"]
    if rule == "point":
        return 1.0 if x_px is not None and homography.point_in_polygon(x_px, y_px, polygon) else 0.0
    if rule == "bbox_overlap":
        return homography.polygon_box_overlap(polygon, ev.get("bbox") or [])
    if rule == "keypoints_inside":
        points = [p for p in (ev.get("keypoints") or []) if float(p.get("confidence", 1)) > 0]
        if not points:
            return 0.0
        inside = sum(homography.point_in_polygon(float(p["x"]), float(p["y"]), polygon)
                     for p in points)
        if inside < view["min_keypoints"]:
            return 0.0
        return inside / len(points)
    return 0.0


def validate_shape(ev: dict) -> None:
    """Raises ValueError with a human-readable message on a structurally invalid
    observation. Callers translate this into their own HTTP error (422 batch-fail
    for /events, item-level rejection for /observations)."""
    bbox = ev.get("bbox")
    if bbox is not None and (len(bbox) != 4 or bbox[2] <= 0 or bbox[3] <= 0):
        raise ValueError("bbox must be [x,y,w,h] with positive width and height")
    for field in ("point_px", "point_map"):
        point = ev.get(field)
        if point is not None:
            if "x" not in point or "y" not in point:
                raise ValueError(f"{field} must contain numeric x and y")
            try:
                float(point["x"]); float(point["y"])
            except (TypeError, ValueError):
                raise ValueError(f"{field} must contain numeric x and y")
    for point in ev.get("keypoints") or []:
        try:
            float(point["x"]); float(point["y"]); float(point.get("confidence", 1))
        except (KeyError, TypeError, ValueError):
            raise ValueError("each keypoint must contain numeric x and y; confidence is optional")
    for numeric_field in ("value", "confidence"):
        value = ev.get(numeric_field)
        if value is not None:
            try:
                value = float(value)
            except (TypeError, ValueError):
                raise ValueError(f"{numeric_field} must be numeric")
            if value != value or value in (float("inf"), float("-inf")):  # NaN / inf
                raise ValueError(f"{numeric_field} must be a finite number")


def enrich_one(ev: dict, context, zone_by_id: dict) -> dict:
    """Run one already-shape-validated observation/event dict through the geometry
    pipeline. Returns the enriched row dict ready for the `events` table INSERT.
    Does not touch the database except through `context` (already loaded once per
    batch) — callers are responsible for persistence, counters, and alerts."""
    zones, cals, surfaces, views_by_source, views_by_id, zone_by_name = context
    x_px = y_px = x_map = y_map = None
    point_kind = ev.get("point_kind")
    bbox = ev.get("bbox")
    point_px = ev.get("point_px")
    keypoints = ev.get("keypoints") or []
    # Deterministic representative-point precedence: 1) explicit point_px,
    # 2) valid foot/ankle keypoints, 3) bottom-center of the bbox, 4) leave empty
    # when only a mask is present (masks are preserved as evidence, never expanded
    # server-side into a point).
    if point_px:
        x_px, y_px = float(point_px["x"]), float(point_px["y"])
        point_kind = point_kind or "unspecified"
    else:
        foot_names = {"left_ankle", "right_ankle", "left_foot", "right_foot"}
        feet = [p for p in keypoints if str(p.get("name", "")).lower() in foot_names
                and float(p.get("confidence", 1)) > 0]
        if feet:
            x_px = sum(float(p["x"]) for p in feet) / len(feet)
            y_px = sum(float(p["y"]) for p in feet) / len(feet)
            point_kind = point_kind or "foot_keypoints"
        elif bbox:
            x_px, y_px = bbox[0] + bbox[2] / 2.0, bbox[1] + bbox[3]
            point_kind = point_kind or "bbox_bottom_center"
        elif ev.get("mask") is not None:
            point_kind = point_kind or "mask_unavailable"

    source_id = ev.get("source_id")
    zone_view_id = ev.get("zone_view_id")
    zone_id = ev.get("zone_id")
    zone_name = ev.get("zone")

    matched_view = None
    if zone_view_id is not None:
        matched_view = views_by_id.get(zone_view_id)
        if not matched_view:
            raise LookupError(f"zone view {zone_view_id} not found")
        if matched_view["source_id"] != source_id:
            raise ValueError("zone view belongs to a different source")
    elif zone_id is None and not zone_name and source_id in views_by_source:
        candidates = []
        for view in views_by_source[source_id]:
            score = view_score(view, ev, x_px, y_px)
            if score >= float(view["threshold"]):
                candidates.append((score, -view["id"], view))
        if candidates:
            matched_view = max(candidates)[2]

    surface_id = ev.get("projection_surface_id")
    if surface_id is None and matched_view:
        surface_id = matched_view["projection_surface_id"]
    surface_revision = calibration_revision = projection_method = None
    surface = None
    if surface_id is not None:
        surface = surfaces.get(surface_id)
        if not surface:
            raise LookupError(f"projection surface {surface_id} not found")
        if surface["source_id"] != source_id:
            raise ValueError("projection surface belongs to a different source")
        surface_revision = surface["revision"]
    point_map = ev.get("point_map")
    if point_map:
        x_map, y_map = float(point_map["x"]), float(point_map["y"])
        projection_method = "worker_point_map"
    elif x_px is not None and surface is not None:
        (x_map, y_map), = homography.project(surface["H"], [[x_px, y_px]])
        projection_method = f"surface:{surface['name']}"
    elif x_px is not None and source_id in cals:
        cal = cals[source_id]
        (x_map, y_map), = homography.project(cal["H"], [[x_px, y_px]])
        calibration_revision = cal["revision"]
        projection_method = "floor"

    assignment_method = "explicit_zone_id" if zone_id is not None else None
    if zone_id is None and zone_name:
        zone_id = zone_by_name.get(str(zone_name).lower())
        if zone_id is None:
            raise LookupError(f"zone '{zone_name}' not found")
        assignment_method = "explicit_zone_name"
    if matched_view and zone_id is not None and matched_view["zone_id"] != zone_id:
        raise ValueError("zone view and explicit zone refer to different zones")
    if zone_id is None and matched_view:
        zone_id = matched_view["zone_id"]
        assignment_method = f"zone_view:{matched_view['membership_rule']}"
    if zone_id is None and x_map is not None:
        for z in zones:
            if homography.point_in_polygon(x_map, y_map, z["polygon"]):
                zone_id = z["id"]
                assignment_method = "map_point"
                break
    if zone_id is not None and zone_id not in zone_by_id:
        raise LookupError(f"zone {zone_id} not found")
    zone_revision = zone_by_id.get(zone_id, {}).get("revision")
    view_id = matched_view["id"] if matched_view else zone_view_id
    view_revision = matched_view["revision"] if matched_view else None

    return {
        "job_id": ev.get("job_id"), "source_id": source_id, "event_type": ev["event_type"],
        "track_id": ev.get("track_id"), "zone_id": zone_id, "x_px": x_px, "y_px": y_px,
        "x_map": x_map, "y_map": y_map, "value": ev.get("value"), "label": ev.get("label"),
        "bbox": bbox, "keypoints": ev.get("keypoints"), "mask": ev.get("mask"),
        "point_kind": point_kind, "projection_surface_id": surface_id,
        "zone_view_id": view_id, "zone_assignment_method": assignment_method,
        "projection_method": projection_method, "zone_revision": zone_revision,
        "calibration_revision": calibration_revision, "surface_revision": surface_revision,
        "zone_view_revision": view_revision, "attributes": ev.get("attributes") or {},
        "schema_version": ev.get("schema_version", 1), "observation_id": ev.get("observation_id"),
        "worker_id": ev.get("worker_id"), "name": ev.get("name"), "entity_type": ev.get("entity_type"),
        "value_kind": ev.get("value_kind"), "unit": ev.get("unit"), "confidence": ev.get("confidence"),
        "identity_scope": ev.get("identity_scope"), "identity_model_version": ev.get("identity_model_version"),
    }


INSERT_SQL = (
    "INSERT INTO events (job_id,source_id,ts,event_type,track_id,zone_id,x_px,y_px,x_map,y_map,"
    " value,label,bbox_json,keypoints_json,mask_json,point_kind,projection_surface_id,zone_view_id,"
    " zone_assignment_method,projection_method,zone_revision,calibration_revision,surface_revision,"
    " zone_view_revision,attributes,created_at,schema_version,observation_id,worker_id,name,"
    " entity_type,value_kind,unit,confidence,identity_scope,identity_model_version)"
    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
)


def update_counters(enriched: list[dict], job_id: int | None) -> None:
    """Shared post-insert bookkeeping: job event_count/last_event_at, and per-source
    event_count/last_ingestion_at/last_observation_at. `enriched` items must already
    carry a `ts` key (the parsed observation timestamp)."""
    if not enriched:
        return
    if job_id is not None:
        db.ex("UPDATE jobs SET event_count=event_count+?, last_event_at=? WHERE id=?",
              (len(enriched), max(e["ts"] for e in enriched), job_id))
    ingested_at = db.now()
    source_ids = {e["source_id"] for e in enriched if e.get("source_id") is not None}
    for source_id in source_ids:
        source_events = [e for e in enriched if e["source_id"] == source_id]
        latest_observation = max(e["ts"] for e in source_events)
        db.ex(
            "UPDATE sources SET event_count=event_count+?, last_ingestion_at=?, "
            "last_observation_at=CASE WHEN last_observation_at IS NULL OR last_observation_at<? "
            "THEN ? ELSE last_observation_at END WHERE id=?",
            (len(source_events), ingested_at, latest_observation, latest_observation, source_id),
        )


_CURRENT_VALUE_EVENT = {
    "detection": "current_detection.updated",
    "measurement": "current_measurement.updated",
    "state": "current_state.updated",
}


def publish_batch(enriched: list[dict], alerts: list[dict], zone_names: dict) -> None:
    """Shared SSE fan-out for a just-inserted batch, capped so a bulk backfill or
    replay doesn't flood connected browsers. Publishes both the legacy event
    names (cv_event/batch_summary/alert) and the current normalized ones
    (observation.created/current_*.updated/alert.created/analysis.invalidated)
    so older dashboard builds and the current one both work against one stream."""
    from .sse import broker
    for e in enriched[:25]:
        payload = {**e, "zone_name": zone_names.get(e.get("zone_id"))}
        broker.publish("cv_event", payload)
        broker.publish("observation.created", payload)
        current_event = _CURRENT_VALUE_EVENT.get(e.get("event_type"))
        if current_event:
            broker.publish(current_event, payload)
    if len(enriched) > 25:
        broker.publish("batch_summary", {"inserted": len(enriched)})
    if enriched:
        broker.publish("analysis.invalidated", {"reason": "observations_ingested", "count": len(enriched)})
    for a in alerts:
        broker.publish("alert", a)
        broker.publish("alert.created", a)


def row_tuple(enriched: dict, ts: float, ingested_at: float) -> tuple:
    """Positional tuple matching INSERT_SQL for one enriched observation."""
    import json
    return (
        enriched["job_id"], enriched["source_id"], ts, enriched["event_type"], enriched["track_id"],
        enriched["zone_id"], enriched["x_px"], enriched["y_px"], enriched["x_map"], enriched["y_map"],
        enriched["value"], enriched["label"], json.dumps(enriched["bbox"]), json.dumps(enriched["keypoints"]),
        json.dumps(enriched["mask"]), enriched["point_kind"], enriched["projection_surface_id"],
        enriched["zone_view_id"], enriched["zone_assignment_method"], enriched["projection_method"],
        enriched["zone_revision"], enriched["calibration_revision"], enriched["surface_revision"],
        enriched["zone_view_revision"], json.dumps(enriched["attributes"]), ingested_at,
        enriched["schema_version"], enriched["observation_id"], enriched["worker_id"], enriched["name"],
        enriched["entity_type"], enriched["value_kind"], enriched["unit"], enriched["confidence"],
        enriched["identity_scope"], enriched["identity_model_version"],
    )
