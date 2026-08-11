"""Legacy event ingestion and querying — kept as a documented compatibility surface.

New workers and new documentation use `POST /observations/batch`
(`server/routers/observations.py`), which enforces the current three-kind contract
(detection | measurement | state) and rejects worker-calculated zone_enter/zone_exit/
zone_dwell/state_change/count. This endpoint still accepts all of those legacy
event_types unchanged, so existing integrations keep working, and both endpoints
share one enrichment implementation (`services/enrich.py`) — there is exactly one
projection/zone-assignment pipeline, not two parallel ones.

Contract note: `zone_dwell` is deprecated — still accepted and stored for backward
compatibility, but its value is ignored by analytics and alerts (dwell is derived
from zone_enter/zone_exit pairs, or from tracked detections — see services/derive.py).
Querying supports keyset pagination via `cursor`.
"""
import asyncio
import json
from datetime import datetime

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .. import db
from ..services import alert_engine, enrich
from ..services.sse import broker

router = APIRouter()

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


@router.post(
    "/events",
    deprecated=True,
    tags=["legacy-events"],
    summary="Submit legacy events (compatibility only)",
    description="Use POST /observations/batch for all new workers.",
)
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

    context = enrich.load_geometry_context()
    zones = context[0]
    zone_by_id = {z["id"]: z for z in zones}
    projected = zone_assigned = zone_view_assigned = 0
    enriched, rows = [], []
    for ev in batch.events:
        if ev.event_type not in EVENT_TYPES:
            raise HTTPException(422, f"event_type '{ev.event_type}' not in {sorted(EVENT_TYPES)}")
        ev_dict = {**ev.model_dump(), "job_id": batch.job_id}
        try:
            enrich.validate_shape(ev_dict)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        ts = _parse_ts(ev.ts)
        try:
            e = enrich.enrich_one(ev_dict, context, zone_by_id)
        except LookupError as exc:
            raise HTTPException(404, str(exc))
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        if e["projection_method"] is not None:
            projected += 1
        if e["zone_assignment_method"] == "map_point":
            zone_assigned += 1
        elif e["zone_assignment_method"] and e["zone_assignment_method"].startswith("zone_view:"):
            zone_view_assigned += 1
        e["ts"] = ts
        enriched.append(e)
        rows.append(enrich.row_tuple(e, ts, db.now()))

    db.exmany(enrich.INSERT_SQL, rows)
    enrich.update_counters(enriched, batch.job_id)

    zone_names = {z["id"]: z["name"] for z in zones}
    alerts = alert_engine.evaluate_batch(enriched, zone_names)
    enrich.publish_batch(enriched, alerts, zone_names)

    return {"inserted": len(rows), "projected": projected, "zone_assigned": zone_assigned,
            "zone_view_assigned": zone_view_assigned, "alerts": len(alerts)}


@router.get(
    "/events",
    deprecated=True,
    tags=["legacy-events"],
    summary="List legacy and underlying event rows",
    description="Compatibility query surface. Prefer GET /observations for schema-v2 data.",
)
def query_events(since: float | None = None, until: float | None = None,
                 event_type: str | None = None, zone_id: int | None = None,
                 source_id: int | None = None, job_id: int | None = None,
                 track_id: str | None = None, label: str | None = None,
                 cursor: str | None = None, limit: int = 200):
    """Query stored events, newest first (legacy surface — see /observations for the
    current contract). Pass the returned `next_cursor` back as `cursor` to fetch the
    next page; `total` counts all rows matching the filters."""
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


@router.get("/stream", tags=["stream"], summary="Stream platform updates using server-sent events")
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
