"""Event ingestion and querying. Ingestion enriches each event:
- bbox -> bottom-center pixel point if no explicit point given
- pixel point -> map meters via the source's homography (if calibrated)
- map point -> zone assignment via point-in-polygon (if no explicit zone)
Then persists, evaluates alert rules, and publishes to the live SSE stream."""
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
    zones = [{"id": z["id"], "name": z["name"], "polygon": db.jload(z["polygon_json"], [])}
             for z in db.q("SELECT id, name, polygon_json FROM zones")]
    cals = {}
    for s in db.q("SELECT id, calibration_json FROM sources WHERE calibration_json IS NOT NULL"):
        cal = db.jload(s["calibration_json"], None)
        if cal and cal.get("H"):
            cals[s["id"]] = cal["H"]
    zone_by_name = {z["name"].lower(): z["id"] for z in zones}
    return zones, cals, zone_by_name


@router.post("/events")
async def ingest(batch: EventBatch):
    if not batch.events:
        return {"inserted": 0, "projected": 0, "zone_assigned": 0, "alerts": 0}
    if len(batch.events) > 5000:
        raise HTTPException(413, "batch too large — send at most 5000 events per request")
    if batch.job_id is not None and not db.q1("SELECT id FROM jobs WHERE id=?", (batch.job_id,)):
        raise HTTPException(404, f"job {batch.job_id} not found — register a job first")

    zones, cals, zone_by_name = _load_context()
    projected = zone_assigned = 0
    enriched, rows = [], []
    for ev in batch.events:
        if ev.event_type not in EVENT_TYPES:
            raise HTTPException(422, f"event_type '{ev.event_type}' not in {sorted(EVENT_TYPES)}")
        ts = _parse_ts(ev.ts)
        x_px = y_px = x_map = y_map = None
        if ev.bbox and not ev.point_px:  # feet position: bottom-center of the box
            x_px, y_px = ev.bbox[0] + ev.bbox[2] / 2.0, ev.bbox[1] + ev.bbox[3]
        if ev.point_px:
            x_px, y_px = float(ev.point_px["x"]), float(ev.point_px["y"])
        if ev.point_map:
            x_map, y_map = float(ev.point_map["x"]), float(ev.point_map["y"])
        elif x_px is not None and ev.source_id in cals:
            (x_map, y_map), = homography.project(cals[ev.source_id], [[x_px, y_px]])
            projected += 1
        zone_id = ev.zone_id
        if zone_id is None and ev.zone:
            zone_id = zone_by_name.get(ev.zone.lower())
        if zone_id is None and x_map is not None:
            for z in zones:
                if homography.point_in_polygon(x_map, y_map, z["polygon"]):
                    zone_id = z["id"]
                    zone_assigned += 1
                    break
        e = {"job_id": batch.job_id, "source_id": ev.source_id, "ts": ts, "event_type": ev.event_type,
             "track_id": ev.track_id, "zone_id": zone_id, "x_px": x_px, "y_px": y_px,
             "x_map": x_map, "y_map": y_map, "value": ev.value, "label": ev.label,
             "attributes": ev.attributes or {}}
        enriched.append(e)
        rows.append((e["job_id"], e["source_id"], ts, e["event_type"], e["track_id"], zone_id,
                     x_px, y_px, x_map, y_map, e["value"], e["label"], json.dumps(e["attributes"]), db.now()))

    db.exmany(
        "INSERT INTO events (job_id, source_id, ts, event_type, track_id, zone_id, x_px, y_px, x_map, y_map, value, label, attributes, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows,
    )
    if batch.job_id is not None:
        db.ex("UPDATE jobs SET event_count=event_count+?, last_event_at=? WHERE id=?",
              (len(rows), max(e["ts"] for e in enriched), batch.job_id))

    zone_names = {z["id"]: z["name"] for z in zones}
    alerts = alert_engine.evaluate(enriched, zone_names)

    # Live stream: cap per-batch fan-out so a bulk backfill doesn't flood browsers.
    for e in enriched[:25]:
        broker.publish("cv_event", {**e, "zone_name": zone_names.get(e["zone_id"])})
    if len(enriched) > 25:
        broker.publish("batch_summary", {"inserted": len(enriched), "job_id": batch.job_id})
    for a in alerts:
        broker.publish("alert", a)

    return {"inserted": len(rows), "projected": projected, "zone_assigned": zone_assigned, "alerts": len(alerts)}


@router.get("/events")
def query_events(since: float | None = None, until: float | None = None,
                 event_type: str | None = None, zone_id: int | None = None,
                 source_id: int | None = None, job_id: int | None = None,
                 track_id: str | None = None, limit: int = 200):
    limit = min(max(1, limit), 5000)
    where, args = ["1=1"], []
    for clause, val in (("ts>=?", since), ("ts<=?", until), ("event_type=?", event_type),
                        ("zone_id=?", zone_id), ("source_id=?", source_id),
                        ("job_id=?", job_id), ("track_id=?", track_id)):
        if val is not None:
            where.append(clause)
            args.append(val)
    rows = db.q(f"SELECT * FROM events WHERE {' AND '.join(where)} ORDER BY ts DESC LIMIT {limit}", args)
    zone_names = {z["id"]: z["name"] for z in db.q("SELECT id, name FROM zones")}
    for r in rows:
        r["attributes"] = db.jload(r["attributes"], {})
        r["zone_name"] = zone_names.get(r["zone_id"])
    return {"events": rows, "count": len(rows)}


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
