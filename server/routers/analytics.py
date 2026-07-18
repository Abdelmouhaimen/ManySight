"""Analytics over the event stream: heatmap, dwell, occupancy, transitions, states, summary.

Everything here is derived from raw observations at read time. Worker-computed
aggregates are never trusted: `zone_dwell` values and `state_change` durations are
ignored — dwell comes from zone_enter/zone_exit pairs, state durations from
consecutive state_change timestamps (see services/derive.py).
"""
from collections import defaultdict

from fastapi import APIRouter, HTTPException

from .. import db
from ..services import derive

router = APIRouter(tags=["analytics"])

DAY = 86400.0


def _range(since, until):
    until = until if until is not None else db.now()
    since = since if since is not None else until - DAY
    return since, until


def _zone_names() -> dict[int, str]:
    return {z["id"]: z["name"] for z in db.q("SELECT id, name FROM zones")}


@router.get("/analytics/summary")
def summary(since: float | None = None, until: float | None = None):
    since, until = _range(since, until)
    events = db.q1("SELECT COUNT(*) n FROM events WHERE ts BETWEEN ? AND ?", (since, until))["n"]
    tracks = db.q1(
        "SELECT COUNT(DISTINCT track_id) n FROM events WHERE ts BETWEEN ? AND ? AND track_id IS NOT NULL",
        (since, until))["n"]
    active_tracks = db.q1(
        "SELECT COUNT(DISTINCT track_id) n FROM events WHERE ts>=? AND track_id IS NOT NULL",
        (db.now() - 300,))["n"]
    jobs_active = db.q1("SELECT COUNT(*) n FROM jobs WHERE status='active'")["n"]
    alerts_unacked = db.q1("SELECT COUNT(*) n FROM alerts WHERE acknowledged=0")["n"]
    active_cutoff = db.now() - 30
    src = db.q1(
        "SELECT COUNT(*) total, "
        "SUM(CASE WHEN last_ingestion_at>=? THEN 1 ELSE 0 END) active FROM sources",
        (active_cutoff,),
    )
    return {
        "since": since, "until": until, "events": events, "tracks": tracks,
        "active_tracks": active_tracks, "jobs_active": jobs_active, "alerts_unacked": alerts_unacked,
        # Keep the old response key for API compatibility; it now means observations
        # were ingested in the last 30 seconds, never that the server opened a feed.
        "sources_online": src["active"] or 0, "sources_active": src["active"] or 0,
        "sources_total": src["total"],
        "zones": db.q1("SELECT COUNT(*) n FROM zones")["n"],
    }


@router.get("/analytics/heatmap")
def heatmap(since: float | None = None, until: float | None = None,
            event_type: str = "detection", job_id: int | None = None,
            source_id: int | None = None, zone_id: int | None = None,
            label: str | None = None, cell: float = 0.25):
    since, until = _range(since, until)
    cell = max(0.05, min(cell, 2.0))
    where, args = ["ts BETWEEN ? AND ?", "x_map IS NOT NULL"], [since, until]
    if event_type:
        where.append("event_type=?"); args.append(event_type)
    if job_id is not None:
        where.append("job_id=?"); args.append(job_id)
    if source_id is not None:
        where.append("source_id=?"); args.append(source_id)
    if zone_id is not None:
        where.append("zone_id=?"); args.append(zone_id)
    if label is not None:
        where.append("label=?"); args.append(label)
    rows = db.q(
        f"SELECT CAST(x_map/{cell} AS INTEGER) cx, CAST(y_map/{cell} AS INTEGER) cy, COUNT(*) w"
        f" FROM events WHERE {' AND '.join(where)} GROUP BY cx, cy ORDER BY w DESC LIMIT 50000", args)
    half = cell / 2.0
    return {"cell": cell, "since": since, "until": until,
            "event_type": event_type, "job_id": job_id, "source_id": source_id,
            "zone_id": zone_id, "label": label,
            "points": [{"x": r["cx"] * cell + half, "y": r["cy"] * cell + half, "w": r["w"]} for r in rows]}


@router.get("/analytics/dwell")
def dwell(since: float | None = None, until: float | None = None, group_by: str | None = None,
          zone_id: int | None = None, max_dwell_s: float = derive.MAX_DWELL_S):
    """Time spent in zones, always derived from zone_enter/zone_exit pairs.

    Worker-posted `zone_dwell` events are stored as observations but never read here.
    Still-open visits (enter without exit yet) are included clipped to `until` and
    reported in `open_visits`; single visits are capped at `max_dwell_s`.
    """
    since, until = _range(since, until)
    visits, open_count = derive.derive_dwells(since, until, zone_id, max_dwell_s)
    agg: dict[tuple, dict] = defaultdict(lambda: {"visits": 0, "total_s": 0.0})
    for d in visits:
        group = str(d["attributes"].get(group_by, "all")) if group_by else "all"
        a = agg[(d["zone_id"], group)]
        a["visits"] += 1
        a["total_s"] += d["value"]
    names = _zone_names()
    out = [{"zone_id": zid, "zone_name": names.get(zid, f"zone {zid}"), "group": grp,
            "visits": a["visits"], "total_s": round(a["total_s"], 1),
            "avg_s": round(a["total_s"] / a["visits"], 1)}
           for (zid, grp), a in sorted(agg.items())]
    return {"rows": out, "derived": True, "open_visits": open_count, "group_by": group_by,
            "zone_id": zone_id, "since": since, "until": until}


@router.get("/analytics/occupancy")
def occupancy(since: float | None = None, until: float | None = None,
              bucket_s: float = 600, zone_id: int | None = None,
              label: str | None = None, group_by: str | None = None,
              event_type: str | None = None, source_id: int | None = None,
              job_id: int | None = None):
    """Distinct tracks seen per time bucket, counted across any zone-assigned event
    type (detections, zone_enter/exit, ...). DISTINCT dedupes, so mixed event types
    for the same track do not inflate the count."""
    since, until = _range(since, until)
    bucket_s = max(bucket_s, (until - since) / 500, 10)  # cap at 500 buckets
    where, args = ["ts BETWEEN ? AND ?", "track_id IS NOT NULL", "zone_id IS NOT NULL"], [since, until]
    if zone_id is not None:
        where.append("zone_id=?"); args.append(zone_id)
    if label is not None:
        where.append("label=?"); args.append(label)
    if event_type is not None:
        where.append("event_type=?"); args.append(event_type)
    if source_id is not None:
        where.append("source_id=?"); args.append(source_id)
    if job_id is not None:
        where.append("job_id=?"); args.append(job_id)
    if group_by not in {None, "label"}:
        raise HTTPException(422, "group_by must be 'label' when provided")
    if group_by == "label":
        where.extend(["label IS NOT NULL", "label!=''"])
    rows = db.q(
        f"SELECT CAST(ts/{bucket_s} AS INTEGER) b, COUNT(DISTINCT track_id) n"
        f" FROM events WHERE {' AND '.join(where)} GROUP BY b", args)
    by_bucket = {r["b"]: r["n"] for r in rows}
    b0, b1 = int(since // bucket_s), int(until // bucket_s)
    series = [{"t": b * bucket_s, "count": by_bucket.get(b, 0)} for b in range(b0, b1 + 1)]
    groups = []
    if group_by == "label":
        grouped_rows = db.q(
            f"SELECT CAST(ts/{bucket_s} AS INTEGER) b, label, COUNT(DISTINCT track_id) n"
            f" FROM events WHERE {' AND '.join(where)} GROUP BY b, label ORDER BY label, b", args)
        labels = sorted({r["label"] for r in grouped_rows})
        values = {(r["label"], r["b"]): r["n"] for r in grouped_rows}
        groups = [{
            "label": class_label,
            "points": [{"t": b * bucket_s, "count": values.get((class_label, b), 0)}
                       for b in range(b0, b1 + 1)],
        } for class_label in labels]
    return {"series": series, "groups": groups, "bucket_s": bucket_s,
            "zone_id": zone_id, "label": label, "group_by": group_by,
            "event_type": event_type, "source_id": source_id, "job_id": job_id,
            "since": since, "until": until}


@router.get("/analytics/counts")
def counts(since: float | None = None, until: float | None = None,
           bucket_s: float = 300, zone_id: int | None = None, job_id: int | None = None,
           source_id: int | None = None, label: str | None = None):
    """Time series for classifier/counting workers.

    A worker posts event_type=count, value=<visible objects>, and a human-readable
    label such as "children". Multiple samples in a bucket are averaged so the
    chart represents a population at a point in time rather than cumulative events.
    """
    since, until = _range(since, until)
    bucket_s = max(bucket_s, (until - since) / 500, 10)
    where, args = ["ts BETWEEN ? AND ?", "event_type='count'", "value IS NOT NULL"], [since, until]
    if zone_id is not None:
        where.append("zone_id=?"); args.append(zone_id)
    if job_id is not None:
        where.append("job_id=?"); args.append(job_id)
    if source_id is not None:
        where.append("source_id=?"); args.append(source_id)
    if label is not None:
        where.append("label=?"); args.append(label)
    rows = db.q(
        f"SELECT CAST(ts/{bucket_s} AS INTEGER) b, COALESCE(NULLIF(label,''),'objects') label,"
        f" AVG(value) value, MIN(value) min_value, MAX(value) max_value, COUNT(*) samples"
        f" FROM events WHERE {' AND '.join(where)} GROUP BY b, label ORDER BY b", args)
    grouped: dict[str, list] = defaultdict(list)
    for r in rows:
        grouped[r["label"]].append({
            "t": r["b"] * bucket_s, "count": round(r["value"], 2),
            "min": r["min_value"], "max": r["max_value"], "samples": r["samples"],
        })
    return {"series": [{"label": class_label, "points": points}
                       for class_label, points in grouped.items()],
            "bucket_s": bucket_s, "zone_id": zone_id, "job_id": job_id,
            "source_id": source_id, "label": label, "since": since, "until": until}


@router.get("/analytics/transitions")
def transitions(since: float | None = None, until: float | None = None, max_gap_s: float = 1800):
    since, until = _range(since, until)
    rows = db.q(
        "SELECT ts, track_id, zone_id FROM events WHERE ts BETWEEN ? AND ? AND event_type='zone_enter'"
        " AND track_id IS NOT NULL AND zone_id IS NOT NULL ORDER BY track_id, ts", (since, until))
    if not rows:  # fall back to zone changes observed in raw detections
        rows = db.q(
            "SELECT ts, track_id, zone_id FROM events WHERE ts BETWEEN ? AND ? AND event_type='detection'"
            " AND track_id IS NOT NULL AND zone_id IS NOT NULL ORDER BY track_id, ts", (since, until))
    links: dict[tuple, int] = defaultdict(int)
    prev_track, prev_zone, prev_ts = None, None, 0.0
    for r in rows:
        if r["track_id"] == prev_track and r["zone_id"] != prev_zone and (r["ts"] - prev_ts) <= max_gap_s:
            links[(prev_zone, r["zone_id"])] += 1
        if r["track_id"] != prev_track or r["zone_id"] != prev_zone:
            prev_track, prev_zone, prev_ts = r["track_id"], r["zone_id"], r["ts"]
    names = _zone_names()
    return {"zones": names,
            "links": [{"from": f, "to": t, "from_name": names.get(f), "to_name": names.get(t), "count": n}
                      for (f, t), n in sorted(links.items(), key=lambda kv: -kv[1])]}


@router.get("/analytics/states")
def states(since: float | None = None, until: float | None = None, source_id: int | None = None):
    since, until = _range(since, until)
    where, args = ["event_type='state_change'", "ts<=?"], [until]
    if source_id is not None:
        where.append("source_id=?"); args.append(source_id)
    rows = db.q(f"SELECT ts, source_id, zone_id, label FROM events WHERE {' AND '.join(where)} ORDER BY ts", args)
    src_names = {s["id"]: s["name"] for s in db.q("SELECT id, name FROM sources")}
    by_src: dict = defaultdict(list)
    for r in rows:
        by_src[r["source_id"]].append(r)
    out = []
    for sid, changes in by_src.items():
        segments, totals = [], defaultdict(float)
        for i, c in enumerate(changes):
            start = max(c["ts"], since)
            end = changes[i + 1]["ts"] if i + 1 < len(changes) else until
            if end <= since or not c["label"]:
                continue
            seg = {"label": c["label"], "start": start, "end": end, "seconds": round(end - start, 1)}
            segments.append(seg)
            totals[c["label"]] += seg["seconds"]
        if segments:
            out.append({"source_id": sid, "source_name": src_names.get(sid, f"source {sid}"),
                        "segments": segments, "totals": {k: round(v, 1) for k, v in totals.items()}})
    return {"series": out, "since": since, "until": until}
