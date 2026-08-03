"""Unified analytics query engine: one endpoint answers a (subject, measures,
filters, grouping) question for any of the three observation kinds, instead of
one hand-picked REST path per chart. Visualization is not part of the analytical
identity — `dashboard/src/analytics.jsx` picks a renderer from the response
`shape`; nothing here knows about charts.

This intentionally reuses the existing per-kind analytics machinery rather than
building a second computation path: `services/derive.py` for visit/state/
measurement derivation, and the same live-from-`events`-table philosophy as
`server/routers/analytics.py` (no precomputed/materialized layer, so results are
always replayable from raw observations and geometry revisions).
"""
from collections import defaultdict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import db
from ..services import derive

router = APIRouter(tags=["analytics-query"])

SUBJECTS = {"detection", "measurement", "state"}
MEASURES_BY_SUBJECT = {
    "detection": {"active_entities", "distinct_entities", "observations", "visits",
                  "average_dwell", "total_dwell", "transition_count", "density"},
    "measurement": {"latest", "minimum", "maximum", "average", "sum", "rate", "samples"},
    "state": {"current", "changes", "duration", "average_duration", "time_percentage"},
}
GROUPING_PRIMARIES = {None, "time", "zone"}
SPLIT_DIMENSIONS = {"label", "entity_type", "entity_id", "source", "state_label", "measurement_name"}
# Any split_by value of the form "attribute:<key>" splits by that worker-reported
# attribute key instead (checked separately since the key set is open-ended).
DAY = 86400.0
MAX_BUCKETS = 500


class RangeIn(BaseModel):
    since: float | str | None = None
    until: float | str | None = None


class GroupingIn(BaseModel):
    primary: str | None = None
    bucket: str = "1h"
    split_by: list[str] = []


class ComparisonIn(BaseModel):
    mode: str | None = None  # None | "previous_period"


class QueryIn(BaseModel):
    subject: str
    measures: list[str]
    filters: dict = {}
    grouping: GroupingIn = GroupingIn()
    range: RangeIn = RangeIn()
    comparison: ComparisonIn = ComparisonIn()


def _parse_bound(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    from datetime import datetime
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()


def _resolve_range(range_in: RangeIn) -> tuple[float, float]:
    until = _parse_bound(range_in.until) if _parse_bound(range_in.until) is not None else db.now()
    since = _parse_bound(range_in.since) if _parse_bound(range_in.since) is not None else until - DAY
    if since >= until:
        raise HTTPException(422, "range.since must be before range.until")
    return since, until


def _bucket_seconds(bucket: str, since: float, until: float) -> float:
    try:
        unit = bucket[-1].lower()
        n = float(bucket[:-1] or 1)
        seconds = n * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]
    except (KeyError, ValueError, IndexError):
        raise HTTPException(422, "grouping.bucket must look like '30s', '5m', '1h', or '1d'")
    return max(seconds, (until - since) / MAX_BUCKETS, 1.0)


def _common_where(subject: str, filters: dict) -> tuple[list[str], list]:
    where, args = [], []
    mapping = {
        "source_ids": "source_id", "zone_ids": "zone_id", "labels": "label",
        "entity_types": "entity_type", "entity_ids": "track_id", "job_ids": "job_id",
    }
    if subject == "measurement":
        mapping["measurement_names"] = "name"
    if subject == "state":
        mapping["state_names"] = "name"
        mapping["state_labels"] = "label"
    for filter_key, column in mapping.items():
        values = filters.get(filter_key)
        if values:
            where.append(f"{column} IN ({','.join('?' for _ in values)})")
            args.extend(values)
    return where, args


def _attribute_predicate(filters: dict):
    attrs = filters.get("attributes") or {}
    if not attrs:
        return None

    def matches(row_attributes_json: str) -> bool:
        parsed = db.jload(row_attributes_json, {})
        return all(str(parsed.get(k)) == str(v) for k, v in attrs.items())
    return matches


def _split_key(row: dict, dimension: str):
    if dimension == "source":
        return row.get("source_id")
    if dimension == "entity_id":
        return row.get("track_id")
    if dimension.startswith("attribute:"):
        return db.jload(row.get("attributes"), {}).get(dimension.split(":", 1)[1])
    return row.get(dimension)  # label, entity_type, state_label(=label), measurement_name(=name)


def _validate(q: QueryIn):
    if q.subject not in SUBJECTS:
        raise HTTPException(422, f"subject must be one of {sorted(SUBJECTS)}")
    if not q.measures:
        raise HTTPException(422, "measures must be a non-empty list")
    allowed = MEASURES_BY_SUBJECT[q.subject]
    invalid = [m for m in q.measures if m not in allowed]
    if invalid:
        raise HTTPException(422, f"measures {invalid} are not valid for subject '{q.subject}' "
                                 f"(allowed: {sorted(allowed)})")
    if q.grouping.primary not in GROUPING_PRIMARIES:
        raise HTTPException(422, "grouping.primary must be null, 'time', or 'zone'")
    for split in q.grouping.split_by:
        if split not in SPLIT_DIMENSIONS and not split.startswith("attribute:"):
            raise HTTPException(422, f"grouping.split_by '{split}' must be one of {sorted(SPLIT_DIMENSIONS)} "
                                     "or 'attribute:<key>'")


# ---------------------------------------------------------------------------
# detection subject
# ---------------------------------------------------------------------------

def _detection_rows(since, until, filters, extra_event_types=("detection",)):
    where, args = _common_where("detection", filters)
    where = ["ts BETWEEN ? AND ?", f"event_type IN ({','.join('?' for _ in extra_event_types)})", *where]
    args = [since, until, *extra_event_types, *args]
    rows = db.q(f"SELECT * FROM events WHERE {' AND '.join(where)} ORDER BY ts, id", args)
    predicate = _attribute_predicate(filters)
    if predicate:
        rows = [r for r in rows if predicate(r.get("attributes"))]
    return rows


def _query_detection(q: QueryIn, since: float, until: float) -> dict:
    filters, grouping = q.filters, q.grouping
    warnings = []
    if "density" in q.measures:
        cell = float(filters.get("cell", 0.25))
        cell = max(0.05, min(cell, 2.0))
        rows = _detection_rows(since, until, filters)
        rows = [r for r in rows if r["x_map"] is not None]
        cells: dict[tuple, int] = defaultdict(int)
        for r in rows:
            cells[(int(r["x_map"] // cell), int(r["y_map"] // cell))] += 1
        half = cell / 2.0
        points = [{"x": cx * cell + half, "y": cy * cell + half, "w": w} for (cx, cy), w in cells.items()]
        if len(q.measures) > 1:
            warnings.append("density ignores other requested measures and any grouping — it always returns a map")
        return {"shape": "heatmap", "dimensions": ["x", "y"], "measures": ["density"],
               "rows": points, "metadata": {"cell": cell, "warnings": warnings}}

    if "transition_count" in q.measures:
        max_gap_s = float(filters.get("max_gap_s", 1800))
        rows = _detection_rows(since, until, filters, extra_event_types=("zone_enter", "detection"))
        rows = [r for r in rows if r["track_id"] and r["zone_id"] is not None]
        rows.sort(key=lambda r: (r["track_id"], r["ts"]))
        links: dict[tuple, int] = defaultdict(int)
        prev_track = prev_zone = None
        prev_ts = 0.0
        for r in rows:
            if r["track_id"] == prev_track and r["zone_id"] != prev_zone and (r["ts"] - prev_ts) <= max_gap_s:
                links[(prev_zone, r["zone_id"])] += 1
            if r["track_id"] != prev_track or r["zone_id"] != prev_zone:
                prev_track, prev_zone, prev_ts = r["track_id"], r["zone_id"], r["ts"]
        names = {z["id"]: z["name"] for z in db.q("SELECT id, name FROM zones")}
        result_rows = [{"zone_from": f, "zone_to": t, "zone_from_name": names.get(f),
                        "zone_to_name": names.get(t), "transition_count": n}
                       for (f, t), n in links.items()]
        if len(q.measures) > 1 or grouping.primary not in (None, "zone"):
            warnings.append("transition_count always returns a zone-pair matrix regardless of grouping")
        return {"shape": "categorical", "dimensions": ["zone_from", "zone_to"], "measures": ["transition_count"],
               "rows": result_rows, "metadata": {"warnings": warnings}}

    dwell_measures = {"visits", "average_dwell", "total_dwell"}
    if dwell_measures & set(q.measures):
        zone_ids = filters.get("zone_ids")
        max_dwell_s = float(filters.get("max_dwell_s", derive.MAX_DWELL_S))
        visits, _ = derive.derive_visits(since, until, None, max_dwell_s)
        if zone_ids:
            visits = [v for v in visits if v["zone_id"] in zone_ids]
        if filters.get("entity_ids"):
            visits = [v for v in visits if v["track_id"] in filters["entity_ids"]]
        predicate = _attribute_predicate(filters)
        if predicate:
            visits = [v for v in visits if all(str(v["attributes"].get(k)) == str(val)
                                               for k, val in filters["attributes"].items())]
        names = {z["id"]: z["name"] for z in db.q("SELECT id, name FROM zones")}

        def summarize(group_visits):
            row = {}
            if "visits" in q.measures:
                row["visits"] = len(group_visits)
            if group_visits:
                total = sum(v["value"] for v in group_visits)
                if "total_dwell" in q.measures:
                    row["total_dwell"] = round(total, 1)
                if "average_dwell" in q.measures:
                    row["average_dwell"] = round(total / len(group_visits), 1)
            else:
                row.update({k: 0 for k in ("total_dwell", "average_dwell") if k in q.measures})
            return row

        if grouping.primary == "zone":
            groups: dict = defaultdict(list)
            for v in visits:
                groups[v["zone_id"]].append(v)
            rows = [{"zone_id": zid, "zone_name": names.get(zid), **summarize(vs)} for zid, vs in groups.items()]
            return {"shape": "categorical", "dimensions": ["zone_id"], "measures": q.measures,
                   "rows": rows, "metadata": {}}
        if grouping.primary == "time":
            bucket_s = _bucket_seconds(grouping.bucket, since, until)
            groups = defaultdict(list)
            for v in visits:
                groups[int(v["t0"] // bucket_s)].append(v)
            b0, b1 = int(since // bucket_s), int(until // bucket_s)
            rows = [{"t": b * bucket_s, **summarize(groups.get(b, []))} for b in range(b0, b1 + 1)]
            return {"shape": "timeseries", "dimensions": ["t"], "measures": q.measures,
                   "rows": rows, "metadata": {"bucket_s": bucket_s}}
        return {"shape": "scalar", "dimensions": [], "measures": q.measures,
               "rows": [summarize(visits)], "metadata": {}}

    # active_entities / distinct_entities / observations: plain counts over
    # (optionally zone-assigned) detections.
    where, args = _common_where("detection", filters)
    base_where = ["event_type='detection'", "ts BETWEEN ? AND ?", *where]
    base_args = [since, until, *args]
    predicate = _attribute_predicate(filters)

    def count_rows(extra_where, extra_args, distinct=True, active_cutoff=None):
        w = list(base_where)
        a = list(base_args)
        if active_cutoff is not None:
            w[1] = "ts BETWEEN ? AND ?"
            a[0], a[1] = active_cutoff, until
        w.extend(extra_where)
        a.extend(extra_args)
        if predicate:
            rows = db.q(f"SELECT track_id, attributes FROM events WHERE {' AND '.join(w)}", a)
            rows = [r for r in rows if predicate(r["attributes"])]
            return len({r["track_id"] for r in rows}) if distinct else len(rows)
        col = "COUNT(DISTINCT track_id)" if distinct else "COUNT(*)"
        return db.q1(f"SELECT {col} n FROM events WHERE {' AND '.join(w)}", a)["n"]

    def summarize_scalar(extra_where=(), extra_args=(), active_cutoff=None):
        row = {}
        if "observations" in q.measures:
            row["observations"] = count_rows(extra_where, extra_args, distinct=False, active_cutoff=active_cutoff)
        if "distinct_entities" in q.measures:
            row["distinct_entities"] = count_rows(extra_where, extra_args, distinct=True, active_cutoff=active_cutoff)
        if "active_entities" in q.measures:
            row["active_entities"] = count_rows(extra_where, extra_args, distinct=True,
                                                active_cutoff=until - derive.PRESENCE_TIMEOUT_S)
        return row

    if grouping.primary == "zone":
        zone_ids = filters.get("zone_ids") or [z["id"] for z in db.q("SELECT id FROM zones")]
        names = {z["id"]: z["name"] for z in db.q("SELECT id, name FROM zones")}
        rows = [{"zone_id": zid, "zone_name": names.get(zid),
                **summarize_scalar(["zone_id=?"], [zid])} for zid in zone_ids]
        return {"shape": "categorical", "dimensions": ["zone_id"], "measures": q.measures, "rows": rows, "metadata": {}}
    if grouping.primary == "time":
        bucket_s = _bucket_seconds(grouping.bucket, since, until)
        b0, b1 = int(since // bucket_s), int(until // bucket_s)
        rows = []
        for b in range(b0, b1 + 1):
            bucket_since, bucket_until = b * bucket_s, (b + 1) * bucket_s
            rows.append({"t": bucket_since, **summarize_scalar(
                ["ts>=?", "ts<?"], [bucket_since, bucket_until])})
        return {"shape": "timeseries", "dimensions": ["t"], "measures": q.measures, "rows": rows,
               "metadata": {"bucket_s": bucket_s}}
    return {"shape": "scalar", "dimensions": [], "measures": q.measures, "rows": [summarize_scalar()], "metadata": {}}


# ---------------------------------------------------------------------------
# measurement subject
# ---------------------------------------------------------------------------

def _query_measurement(q: QueryIn, since: float, until: float) -> dict:
    filters, grouping = q.filters, q.grouping
    names = filters.get("measurement_names") or [
        r["name"] for r in db.q(
            "SELECT DISTINCT name FROM events WHERE event_type='measurement' AND name IS NOT NULL"
            " AND ts BETWEEN ? AND ?", (since, until))
    ]
    if not names:
        return {"shape": "scalar" if not grouping.primary else "timeseries", "dimensions": [],
               "measures": q.measures, "rows": [], "metadata": {"warnings": ["no measurement series matched"]}}

    def filtered_series(name):
        rows = derive.measurement_series(since, until, name)
        if filters.get("source_ids"):
            rows = [r for r in rows if r.get("source_id") in filters["source_ids"]]
        if filters.get("entity_ids"):
            rows = [r for r in rows if r.get("track_id") in filters["entity_ids"]]
        if filters.get("labels"):
            rows = [r for r in rows if r.get("label") in filters["labels"]]
        predicate = _attribute_predicate(filters)
        if predicate:
            rows = [r for r in rows if predicate(r.get("attributes"))]
        return rows

    def aggregate_for(name):
        agg = derive.aggregate_measurement(filtered_series(name))
        return {m: agg[m] for m in q.measures}

    if grouping.primary == "time":
        bucket_s = _bucket_seconds(grouping.bucket, since, until)
        b0, b1 = int(since // bucket_s), int(until // bucket_s)
        rows = []
        for name in names:
            series_rows = filtered_series(name)
            buckets: dict = defaultdict(list)
            for r in series_rows:
                buckets[int(r["ts"] // bucket_s)].append(r)
            for b in range(b0, b1 + 1):
                agg = derive.aggregate_measurement(buckets.get(b, []))
                rows.append({"t": b * bucket_s, "measurement_name": name,
                            **{m: agg[m] for m in q.measures}})
        return {"shape": "timeseries", "dimensions": ["t", "measurement_name"], "measures": q.measures,
               "rows": rows, "metadata": {"bucket_s": bucket_s}}
    rows = [{"measurement_name": name, **aggregate_for(name)} for name in names]
    shape = "categorical" if len(names) > 1 or "measurement_name" in grouping.split_by else "scalar"
    return {"shape": shape, "dimensions": ["measurement_name"] if shape == "categorical" else [],
           "measures": q.measures, "rows": rows, "metadata": {}}


# ---------------------------------------------------------------------------
# state subject
# ---------------------------------------------------------------------------

def _query_state(q: QueryIn, since: float, until: float) -> dict:
    filters = q.filters
    keys = derive.state_keys(since, until, filters.get("source_ids", [None])[0]
                             if len(filters.get("source_ids", [])) == 1 else None)
    if filters.get("state_names"):
        keys = [k for k in keys if k[1] in filters["state_names"]]
    if filters.get("source_ids"):
        keys = [k for k in keys if k[0] in filters["source_ids"]]
    rows = []
    for source_id, name, entity_id in keys:
        samples = derive.state_samples(source_id, name, entity_id, since, until)
        intervals, stale = derive.coalesce_state_intervals(samples, until)
        if filters.get("state_labels"):
            intervals = [iv for iv in intervals if iv["label"] in filters["state_labels"]]
        row = {"source_id": source_id, "name": name, "entity_id": entity_id}
        if "current" in q.measures:
            row["current"] = intervals[-1]["label"] if intervals else None
        if "changes" in q.measures:
            row["changes"] = max(0, len(intervals) - 1)
        totals: dict = defaultdict(float)
        for iv in intervals:
            totals[iv["label"]] += max(0.0, min(iv["end"], until) - max(iv["start"], since))
        total_time = sum(totals.values()) or 1.0
        if "duration" in q.measures:
            row["duration"] = {k: round(v, 1) for k, v in totals.items()}
        if "average_duration" in q.measures:
            row["average_duration"] = {
                label: round(totals[label] / sum(1 for iv in intervals if iv["label"] == label), 1)
                for label in totals if any(iv["label"] == label for iv in intervals)
            }
        if "time_percentage" in q.measures:
            row["time_percentage"] = {k: round(100 * v / total_time, 1) for k, v in totals.items()}
        rows.append(row)
    return {"shape": "categorical", "dimensions": ["source_id", "name", "entity_id"],
           "measures": q.measures, "rows": rows, "metadata": {}}


@router.post("/analytics/query")
def query_analytics(q: QueryIn):
    _validate(q)
    since, until = _resolve_range(q.range)
    handler = {"detection": _query_detection, "measurement": _query_measurement, "state": _query_state}[q.subject]
    result = handler(q, since, until)
    result["metadata"] = {**result.get("metadata", {}), "since": since, "until": until,
                          "derived_from": q.subject, "timezone": "UTC"}
    if q.comparison.mode == "previous_period":
        span = until - since
        previous = handler(q, since - span, since)
        result["comparison"] = {"mode": "previous_period", "since": since - span, "until": since,
                                "rows": previous["rows"]}
    return result


@router.get("/analytics/capabilities")
def capabilities():
    """What the dashboard/MCP client can build a question from: subjects, their
    measures, groupings, split dimensions, and the labels/sources/zones/
    measurement/state names actually present, so the frontend never has to
    duplicate server compatibility rules."""
    return {
        "subjects": sorted(SUBJECTS),
        "measures_by_subject": {k: sorted(v) for k, v in MEASURES_BY_SUBJECT.items()},
        "groupings": {"primary": [None, "time", "zone"], "bucket_examples": ["1m", "5m", "1h", "1d"]},
        "split_dimensions": sorted(SPLIT_DIMENSIONS) + ["attribute:<key>"],
        "sources": db.q("SELECT id, name FROM sources ORDER BY id"),
        "zones": db.q("SELECT id, name FROM zones ORDER BY id"),
        "labels": [r["label"] for r in db.q(
            "SELECT DISTINCT label FROM events WHERE label IS NOT NULL AND label!='' ORDER BY label")],
        "measurement_names": [r["name"] for r in db.q(
            "SELECT DISTINCT name FROM events WHERE event_type='measurement' AND name IS NOT NULL ORDER BY name")],
        "state_names": [r["name"] for r in db.q(
            "SELECT DISTINCT name FROM events WHERE event_type='state' AND name IS NOT NULL ORDER BY name")],
        "attribute_keys": sorted({
            k for r in db.q("SELECT attributes FROM events WHERE attributes!='{}' ORDER BY id DESC LIMIT 2000")
            for k in db.jload(r["attributes"], {})
        }),
    }
