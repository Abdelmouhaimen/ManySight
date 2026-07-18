"""Event ingestion and querying. Ingestion preserves bbox/keypoint/mask evidence,
selects a floor or named-plane projection, assigns zones through per-camera decision
ROIs or global map polygons, and records geometry revisions. It then evaluates alert
rules and publishes to the live SSE stream.

Contract note: workers post raw observations, never computed aggregates.
`zone_dwell` is deprecated — still accepted and stored for backward compatibility,
but its value is ignored by analytics and alerts (dwell is derived from
zone_enter/zone_exit pairs). Querying supports keyset pagination via `cursor`."""
import asyncio
import json
from datetime import datetime

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .. import db
from ..services import alert_engine, homography
from ..services.sse import broker

router = APIRouter(tags=["events"])

EVENT_TYPES = {"detection", "zone_enter", "zone_exit", "zone_dwell", "transition", "state_change", "count", "custom"}


class EventIn(BaseModel):
    ts: float | str | None = None
    source_id: int | None = None
    event_type: str = "detection"
    track_id: str | None = None
    zone_id: int | None = None
    zone: str | None = None          # zone name — resolved server-side
    point_px: dict | None = None     # {x,y}
    point_map: dict | None = None    # {x,y} meters
    bbox: list[float] | None = None  # [x,y,w,h] pixels
    keypoints: list[dict] | None = None  # [{x,y,name?,confidence?}, ...]
    mask: dict | str | None = None       # compressed/RLE mask evidence; not expanded server-side
    point_kind: str | None = None        # feet | hip_center | torso_center | mask_centroid | custom
    projection_surface_id: int | None = None  # null = source floor calibration
    zone_view_id: int | None = None      # optional explicit camera ROI provenance
    value: float | None = None
    label: str | None = None
    attributes: dict = {}


class EventBatch(BaseModel):
    job_id: int | None = None
    events: list[EventIn]


def _parse_ts(ts) -> float:
    if ts is None:
        return db.now()
    if isinstance(ts, (int, float)):
        return float(ts)
    return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()


def _load_context():
    zones = [{"id": z["id"], "name": z["name"], "revision": z["revision"],
              "polygon": db.jload(z["polygon_json"], [])}
             for z in db.q("SELECT id, name, polygon_json, revision FROM zones")]
    cals = {}
    for s in db.q("SELECT id, calibration_json, calibration_revision FROM sources"):
        cal = db.jload(s["calibration_json"], None)
        if cal and cal.get("H"):
            cals[s["id"]] = {"H": cal["H"], "revision": s["calibration_revision"]}
    surfaces = {r["id"]: {**r, "H": db.jload(r["homography_json"], None)}
                for r in db.q("SELECT * FROM projection_surfaces")}
    views_by_source = {}
    views_by_id = {}
    for r in db.q("SELECT * FROM zone_views ORDER BY id"):
        view = {**r, "outer": db.jload(r["outer_polygon_json"], []),
                "detection": db.jload(r["detection_polygon_json"], [])}
        views_by_source.setdefault(r["source_id"], []).append(view)
        views_by_id[r["id"]] = view
    zone_by_name = {z["name"].lower(): z["id"] for z in zones}
    return zones, cals, surfaces, views_by_source, views_by_id, zone_by_name


def _view_score(view: dict, ev: EventIn, x_px: float | None, y_px: float | None) -> float:
    polygon = view["detection"] or view["outer"]
    rule = view["membership_rule"]
    if rule == "point":
        return 1.0 if x_px is not None and homography.point_in_polygon(x_px, y_px, polygon) else 0.0
    if rule == "bbox_overlap":
        return homography.polygon_box_overlap(polygon, ev.bbox or [])
    if rule == "keypoints_inside":
        points = [p for p in (ev.keypoints or []) if float(p.get("confidence", 1)) > 0]
        if not points:
            return 0.0
        inside = sum(homography.point_in_polygon(float(p["x"]), float(p["y"]), polygon)
                     for p in points)
        if inside < view["min_keypoints"]:
            return 0.0
        return inside / len(points)
    return 0.0


@router.post("/events")
async def ingest(batch: EventBatch):
    if not batch.events:
        return {"inserted": 0, "projected": 0, "zone_assigned": 0,
                "zone_view_assigned": 0, "alerts": 0}
    if len(batch.events) > 5000:
        raise HTTPException(413, "batch too large — send at most 5000 events per request")
    if batch.job_id is not None and not db.q1("SELECT id FROM jobs WHERE id=?", (batch.job_id,)):
        raise HTTPException(404, f"job {batch.job_id} not found — register a job first")
    source_ids = {ev.source_id for ev in batch.events if ev.source_id is not None}
    known_sources = (
        {
            row["id"]
            for row in db.q(
                f"SELECT id FROM sources WHERE id IN ({','.join('?' for _ in source_ids)})",
                tuple(source_ids),
            )
        }
        if source_ids else set()
    )
    missing_sources = sorted(source_ids - known_sources)
    if missing_sources:
        raise HTTPException(404, f"unknown source ids {missing_sources} — create sources first")

    zones, cals, surfaces, views_by_source, views_by_id, zone_by_name = _load_context()
    zone_by_id = {z["id"]: z for z in zones}
    projected = zone_assigned = zone_view_assigned = 0
    enriched, rows = [], []
    for ev in batch.events:
        if ev.event_type not in EVENT_TYPES:
            raise HTTPException(422, f"event_type '{ev.event_type}' not in {sorted(EVENT_TYPES)}")
        if ev.bbox is not None and (len(ev.bbox) != 4 or ev.bbox[2] <= 0 or ev.bbox[3] <= 0):
            raise HTTPException(422, "bbox must be [x,y,w,h] with positive width and height")
        for field, point in (("point_px", ev.point_px), ("point_map", ev.point_map)):
            if point is not None and ("x" not in point or "y" not in point):
                raise HTTPException(422, f"{field} must contain numeric x and y")
            if point is not None:
                try:
                    float(point["x"]); float(point["y"])
                except (TypeError, ValueError):
                    raise HTTPException(422, f"{field} must contain numeric x and y")
        for point in ev.keypoints or []:
            try:
                float(point["x"]); float(point["y"]); float(point.get("confidence", 1))
            except (KeyError, TypeError, ValueError):
                raise HTTPException(422, "each keypoint must contain numeric x and y; confidence is optional")
        ts = _parse_ts(ev.ts)
        x_px = y_px = x_map = y_map = None
        point_kind = ev.point_kind
        if ev.bbox and not ev.point_px:  # feet position: bottom-center of the box
            x_px, y_px = ev.bbox[0] + ev.bbox[2] / 2.0, ev.bbox[1] + ev.bbox[3]
            point_kind = point_kind or "bbox_bottom_center"
        if ev.point_px:
            x_px, y_px = float(ev.point_px["x"]), float(ev.point_px["y"])
            point_kind = point_kind or "unspecified"

        matched_view = None
        if ev.zone_view_id is not None:
            matched_view = views_by_id.get(ev.zone_view_id)
            if not matched_view:
                raise HTTPException(404, f"zone view {ev.zone_view_id} not found")
            if matched_view["source_id"] != ev.source_id:
                raise HTTPException(422, "zone view belongs to a different source")
        elif ev.zone_id is None and not ev.zone and ev.source_id in views_by_source:
            candidates = []
            for view in views_by_source[ev.source_id]:
                score = _view_score(view, ev, x_px, y_px)
                if score >= float(view["threshold"]):
                    candidates.append((score, -view["id"], view))
            if candidates:
                matched_view = max(candidates)[2]

        surface_id = ev.projection_surface_id
        if surface_id is None and matched_view:
            surface_id = matched_view["projection_surface_id"]
        surface_revision = None
        calibration_revision = None
        projection_method = None
        surface = None
        if surface_id is not None:
            surface = surfaces.get(surface_id)
            if not surface:
                raise HTTPException(404, f"projection surface {surface_id} not found")
            if surface["source_id"] != ev.source_id:
                raise HTTPException(422, "projection surface belongs to a different source")
            surface_revision = surface["revision"]
        if ev.point_map:
            x_map, y_map = float(ev.point_map["x"]), float(ev.point_map["y"])
            projection_method = "worker_point_map"
        elif x_px is not None and surface is not None:
            (x_map, y_map), = homography.project(surface["H"], [[x_px, y_px]])
            projection_method = f"surface:{surface['name']}"
            projected += 1
        elif x_px is not None and ev.source_id in cals:
            cal = cals[ev.source_id]
            (x_map, y_map), = homography.project(cal["H"], [[x_px, y_px]])
            calibration_revision = cal["revision"]
            projection_method = "floor"
            projected += 1
        zone_id = ev.zone_id
        assignment_method = "explicit_zone_id" if zone_id is not None else None
        if zone_id is None and ev.zone:
            zone_id = zone_by_name.get(ev.zone.lower())
            if zone_id is None:
                raise HTTPException(404, f"zone '{ev.zone}' not found")
            assignment_method = "explicit_zone_name"
        if matched_view and zone_id is not None and matched_view["zone_id"] != zone_id:
            raise HTTPException(422, "zone view and explicit zone refer to different zones")
        if zone_id is None and matched_view:
            zone_id = matched_view["zone_id"]
            assignment_method = f"zone_view:{matched_view['membership_rule']}"
            zone_view_assigned += 1
        if zone_id is None and x_map is not None:
            for z in zones:
                if homography.point_in_polygon(x_map, y_map, z["polygon"]):
                    zone_id = z["id"]
                    assignment_method = "map_point"
                    zone_assigned += 1
                    break
        if zone_id is not None and zone_id not in zone_by_id:
            raise HTTPException(404, f"zone {zone_id} not found")
        zone_revision = zone_by_id.get(zone_id, {}).get("revision")
        view_id = matched_view["id"] if matched_view else ev.zone_view_id
        view_revision = matched_view["revision"] if matched_view else None
        e = {"job_id": batch.job_id, "source_id": ev.source_id, "ts": ts, "event_type": ev.event_type,
             "track_id": ev.track_id, "zone_id": zone_id, "x_px": x_px, "y_px": y_px,
             "x_map": x_map, "y_map": y_map, "value": ev.value, "label": ev.label,
             "bbox": ev.bbox, "keypoints": ev.keypoints, "mask": ev.mask,
             "point_kind": point_kind, "projection_surface_id": surface_id,
             "zone_view_id": view_id, "zone_assignment_method": assignment_method,
             "projection_method": projection_method, "zone_revision": zone_revision,
             "calibration_revision": calibration_revision, "surface_revision": surface_revision,
             "zone_view_revision": view_revision,
             "attributes": ev.attributes or {}}
        enriched.append(e)
        rows.append((e["job_id"], e["source_id"], ts, e["event_type"], e["track_id"], zone_id,
                     x_px, y_px, x_map, y_map, e["value"], e["label"], json.dumps(ev.bbox),
                     json.dumps(ev.keypoints), json.dumps(ev.mask), point_kind, surface_id, view_id,
                     assignment_method, projection_method, zone_revision, calibration_revision, surface_revision,
                     view_revision, json.dumps(e["attributes"]), db.now()))

    db.exmany(
        "INSERT INTO events (job_id,source_id,ts,event_type,track_id,zone_id,x_px,y_px,x_map,y_map,"
        " value,label,bbox_json,keypoints_json,mask_json,point_kind,projection_surface_id,zone_view_id,"
        " zone_assignment_method,projection_method,zone_revision,calibration_revision,surface_revision,zone_view_revision,"
        " attributes,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows,
    )
    if batch.job_id is not None:
        db.ex("UPDATE jobs SET event_count=event_count+?, last_event_at=? WHERE id=?",
              (len(rows), max(e["ts"] for e in enriched), batch.job_id))
    ingested_at = db.now()
    for source_id in source_ids:
        source_events = [event for event in enriched if event["source_id"] == source_id]
        latest_observation = max(event["ts"] for event in source_events)
        db.ex(
            "UPDATE sources SET event_count=event_count+?, last_ingestion_at=?, "
            "last_observation_at=CASE WHEN last_observation_at IS NULL OR last_observation_at<? "
            "THEN ? ELSE last_observation_at END WHERE id=?",
            (len(source_events), ingested_at, latest_observation, latest_observation, source_id),
        )

    zone_names = {z["id"]: z["name"] for z in zones}
    alerts = alert_engine.evaluate(enriched, zone_names)

    # Live stream: cap per-batch fan-out so a bulk backfill doesn't flood browsers.
    for e in enriched[:25]:
        broker.publish("cv_event", {**e, "zone_name": zone_names.get(e["zone_id"])})
    if len(enriched) > 25:
        broker.publish("batch_summary", {"inserted": len(enriched), "job_id": batch.job_id})
    for a in alerts:
        broker.publish("alert", a)

    return {"inserted": len(rows), "projected": projected, "zone_assigned": zone_assigned,
            "zone_view_assigned": zone_view_assigned, "alerts": len(alerts)}


@router.get("/events")
def query_events(since: float | None = None, until: float | None = None,
                 event_type: str | None = None, zone_id: int | None = None,
                 source_id: int | None = None, job_id: int | None = None,
                 track_id: str | None = None, label: str | None = None,
                 cursor: str | None = None, limit: int = 200):
    """Query stored events, newest first. Pass the returned `next_cursor` back as
    `cursor` to fetch the next page; `total` counts all rows matching the filters."""
    limit = min(max(1, limit), 5000)
    where, args = ["1=1"], []
    for clause, val in (("ts>=?", since), ("ts<=?", until), ("event_type=?", event_type),
                        ("zone_id=?", zone_id), ("source_id=?", source_id),
                        ("job_id=?", job_id), ("track_id=?", track_id), ("label=?", label)):
        if val is not None:
            where.append(clause)
            args.append(val)
    total = db.q1(f"SELECT COUNT(*) n FROM events WHERE {' AND '.join(where)}", args)["n"]
    if cursor is not None:
        try:
            ts_part, id_part = cursor.rsplit(":", 1)
            cur_ts, cur_id = float(ts_part), int(id_part)
        except (ValueError, TypeError):
            raise HTTPException(422, "malformed cursor — pass a next_cursor value verbatim")
        where.append("(ts<? OR (ts=? AND id<?))")
        args.extend([cur_ts, cur_ts, cur_id])
    rows = db.q(f"SELECT * FROM events WHERE {' AND '.join(where)} ORDER BY ts DESC, id DESC LIMIT {limit}", args)
    zone_names = {z["id"]: z["name"] for z in db.q("SELECT id, name FROM zones")}
    for r in rows:
        r["attributes"] = db.jload(r["attributes"], {})
        r["bbox"] = db.jload(r.get("bbox_json"), None)
        r["keypoints"] = db.jload(r.get("keypoints_json"), None)
        r["mask"] = db.jload(r.get("mask_json"), None)
        r.pop("bbox_json", None)
        r.pop("keypoints_json", None)
        r.pop("mask_json", None)
        r["zone_name"] = zone_names.get(r["zone_id"])
    next_cursor = f"{rows[-1]['ts']!r}:{rows[-1]['id']}" if len(rows) == limit else None
    return {"events": rows, "count": len(rows), "total": total, "next_cursor": next_cursor}


@router.get("/stream")
async def stream():
    q = broker.subscribe()

    async def gen():
        try:
            yield "retry: 3000\n\n"
            while True:
                try:
                    yield await asyncio.wait_for(q.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            broker.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
