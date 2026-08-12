"""Unified analytics query engine: one endpoint answers a (subject, measures,
filters, grouping) question for any of the three observation kinds, instead of
one hand-picked REST path per chart. Visualization is not part of the analytical
identity — `dashboard/src/analytics.jsx` picks a renderer from the response
`shape`; nothing here knows about charts.

Detection, measurement, and state subjects reuse the existing deterministic
derivation machinery. Fused current occupancy and history read the bounded,
persisted materializations maintained when complete source samples arrive.
"""
from collections import defaultdict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import db
from ..services import derive

router = APIRouter(tags=["analytics-query"])

SUBJECTS = {"detection", "measurement", "state", "fused_entity"}
MEASURES_BY_SUBJECT = {
    "detection": {"active_entities", "distinct_entities", "observations", "visits",
                  "average_dwell", "total_dwell", "transition_count", "density"},
    "measurement": {"latest", "minimum", "maximum", "average", "sum", "rate", "samples"},
    "state": {"current", "changes", "duration", "average_duration", "time_percentage"},
    "fused_entity": {"current_occupancy", "current_entities"},
}
GROUPING_PRIMARIES = {None, "time", "zone"}
SPLIT_DIMENSIONS = {"label", "entity_type", "entity_id", "source", "state_label", "measurement_name"}
# Any split_by value of the form "attribute:<key>" splits by that worker-reported
# attribute key instead (checked separately since the key set is open-ended).
DAY = 86400.0
MAX_BUCKETS = 500
DETECTION_FRAME_COUNT_NAME = "detection_frame_count"


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


def _query_fused_entity(q: QueryIn, _since: float, _until: float) -> dict:
    """Query persisted fused current state, never a recent raw-detection window."""
    from ..services import multiview
    multiview.refresh_freshness()
    group_ids = q.filters.get("group_ids") or [row["id"] for row in db.q(
        "SELECT id FROM multiview_groups WHERE enabled=1 ORDER BY id")]
    entity_types = q.filters.get("entity_types") or ["person"]
    zone_ids = q.filters.get("zone_ids") or []
    if len(group_ids) != 1:
        raise HTTPException(422, "fused_entity queries require exactly one multiview group_id")
    group_id = int(group_ids[0])
    if q.grouping.primary == "time":
        if len(zone_ids) != 1:
            raise HTTPException(422, "fused occupancy time-series requires exactly one zone_id")
        bucket_s = _bucket_seconds(q.grouping.bucket, _since, _until)
        history = db.q(
            "SELECT * FROM zone_occupancy_observations WHERE group_id=? AND zone_id=? "
            "AND entity_type=? AND ts>=? AND ts<=? ORDER BY ts,id",
            (group_id, int(zone_ids[0]), entity_types[0], _since, _until),
        )
        latest_by_bucket = {}
        for row in history:
            bucket = _since + int((row["ts"] - _since) // bucket_s) * bucket_s
            latest_by_bucket[bucket] = row
        rows = []
        for bucket, row in sorted(latest_by_bucket.items()):
            result = {"timestamp": bucket, "quality": row["quality"], "as_of": row["ts"]}
            for measure in q.measures:
                result[measure] = row["value"]
            rows.append(result)
        return {"shape": "timeseries", "dimensions": ["timestamp"], "measures": q.measures,
                "rows": rows, "metadata": {"group_id": group_id, "zone_id": int(zone_ids[0]),
                                             "identity": "anonymous fused track",
                                             "bucket_seconds": bucket_s}}
    if q.grouping.primary == "zone":
        zones = zone_ids or [row["id"] for row in db.q("SELECT id FROM zones ORDER BY id")]
        names = {row["id"]: row["name"] for row in db.q("SELECT id,name FROM zones")}
        rows = []
        for zone_id in zones:
            state = db.q1(
                "SELECT * FROM zone_current_occupancy WHERE group_id=? AND zone_id=? AND entity_type=?",
                (group_id, zone_id, entity_types[0]),
            )
            rows.append({"zone_id": zone_id, "zone_name": names.get(zone_id),
                         "current_occupancy": state["value"] if state else None,
                         "quality": state["quality"] if state else "unknown",
                         "as_of": state["as_of"] if state else db.now()})
        return {"shape": "categorical", "dimensions": ["zone_id"], "measures": q.measures,
                "rows": rows, "metadata": {"group_id": group_id, "identity": "anonymous fused track"}}
    if len(zone_ids) != 1:
        raise HTTPException(422, "scalar current fused occupancy requires exactly one zone_id")
    state = db.q1(
        "SELECT * FROM zone_current_occupancy WHERE group_id=? AND zone_id=? AND entity_type=?",
        (group_id, int(zone_ids[0]), entity_types[0]),
    )
    row = {
        "current_occupancy": state["value"] if state else None,
        "current_entities": state["value"] if state else None,
        "quality": state["quality"] if state else "unknown",
        "as_of": state["as_of"] if state else db.now(),
    }
    return {"shape": "scalar", "dimensions": [], "measures": q.measures,
            "rows": [{key: value for key, value in row.items() if key in set(q.measures) | {"quality", "as_of"}}],
            "metadata": {"group_id": group_id, "zone_id": int(zone_ids[0]),
                         "identity": "anonymous fused track", "current_state": True}}


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
        if q.measures == ["active_entities"]:
            # A detection-frame measurement is an instantaneous count for one
            # source and entity type. Preserve its producer timestamp exactly:
            # there is deliberately no time bucketing or cross-source merging.
            marker_where = ["event_type='measurement'", "name=?", "ts BETWEEN ? AND ?"]
            marker_args = [DETECTION_FRAME_COUNT_NAME, since, until]
            entity_types = filters.get("entity_types") or []
            if entity_types:
                marker_where.append(f"label IN ({','.join('?' for _ in entity_types)})")
                marker_args.extend(entity_types)
            for filter_key, column in (("source_ids", "source_id"), ("job_ids", "job_id")):
                values = filters.get(filter_key)
                if values:
                    marker_where.append(f"{column} IN ({','.join('?' for _ in values)})")
                    marker_args.extend(values)

            marker_rows = db.q(
                f"SELECT id, ts, source_id, value FROM events "
                f"WHERE {' AND '.join(marker_where)} ORDER BY ts, source_id, id",
                marker_args,
            )

            # Exact-timestamp detection counts remain useful for historical
            # workers without frame-count measurements and for filters (zone or
            # attributes) whose scoped count cannot be read from a whole-frame
            # marker. Workers use one shared timestamp for a frame's detections.
            identities_by_sample = defaultdict(set)
            for event in db.q(
                f"SELECT ts, track_id, identity_scope, source_id, worker_id, attributes "
                f"FROM events WHERE {' AND '.join(base_where)} AND track_id IS NOT NULL "
                f"ORDER BY ts, source_id",
                base_args,
            ):
                if predicate and not predicate(event["attributes"]):
                    continue
                scope = event.get("identity_scope") or "worker_run"
                if scope == "workspace":
                    identity = ("workspace", event["track_id"])
                elif scope == "source":
                    identity = ("source", event["source_id"], event["track_id"])
                else:
                    identity = (
                        "worker",
                        event.get("worker_id") or f"source-{event['source_id']}",
                        event["track_id"],
                    )
                identities_by_sample[(event["ts"], event["source_id"])].add(identity)
            detection_counts = {
                sample: len(identities) for sample, identities in identities_by_sample.items()
            }

            scoped_count = bool(filters.get("zone_ids") or predicate)
            marker_samples = {(marker["ts"], marker["source_id"]) for marker in marker_rows}
            samples = []
            for marker in marker_rows:
                sample = (marker["ts"], marker["source_id"])
                count = detection_counts.get(sample, 0) if scoped_count else int(marker["value"])
                samples.append((marker["ts"], marker["source_id"], marker["id"], count))
            for (timestamp, source_id), count in detection_counts.items():
                if (timestamp, source_id) not in marker_samples:
                    samples.append((timestamp, source_id, 0, count))
            samples.sort()

            result_rows = []
            source_ids = {source_id for _, source_id, _, _ in samples}
            expose_source = len(source_ids) > 1
            for timestamp, source_id, _, count in samples:
                row = {"t": timestamp, "active_entities": count}
                if expose_source:
                    row["source_id"] = source_id
                result_rows.append(row)
            return {
                "shape": "timeseries",
                "dimensions": ["t", "source_id"] if expose_source else ["t"],
                "measures": q.measures,
                "rows": result_rows,
                "metadata": {
                    "active_entity_semantics": "instantaneous camera/entity-type count at each exact producer timestamp",
                    "timestamp_semantics": "exact producer timestamps; no time bucketing or cross-source aggregation",
                    "zero_semantics": "zero only when the source explicitly posted a zero detection-frame count",
                    "source_points": len(result_rows),
                    "sampled": False,
                },
            }

        bucket_s = _bucket_seconds(grouping.bucket, since, until)
        b0, b1 = int(since // bucket_s), int(until // bucket_s)
        active_by_bucket = {}
        if "active_entities" in q.measures:
            identity_key = (
                "CASE COALESCE(identity_scope, 'worker_run') "
                "WHEN 'workspace' THEN 'workspace:' || track_id "
                "WHEN 'source' THEN 'source:' || source_id || ':' || track_id "
                "ELSE 'worker:' || COALESCE(CAST(worker_id AS TEXT), 'source-' || source_id) || ':' || track_id END"
            )
            if predicate:
                # Attribute predicates are intentionally evaluated in Python;
                # all other common filters stay in the SQL base predicate.
                samples = defaultdict(set)
                for event in db.q(
                    f"SELECT ts, track_id, identity_scope, source_id, worker_id, attributes "
                    f"FROM events WHERE {' AND '.join(base_where)} AND track_id IS NOT NULL",
                    base_args,
                ):
                    if not predicate(event["attributes"]):
                        continue
                    scope = event.get("identity_scope") or "worker_run"
                    if scope == "workspace":
                        key = ("workspace", event["track_id"])
                    elif scope == "source":
                        key = ("source", event["source_id"], event["track_id"])
                    else:
                        key = ("worker", event.get("worker_id") or f"source-{event['source_id']}", event["track_id"])
                    samples[int(event["ts"])].add(key)
                for sample_second, identities in samples.items():
                    bucket_id = int(sample_second // bucket_s)
                    active_by_bucket[bucket_id] = max(active_by_bucket.get(bucket_id, 0), len(identities))
            else:
                # First count scoped identities in each one-second frame bin,
                # then take the peak frame count in each coarser chart bucket.
                # We never union track IDs across the whole display bucket.
                rows = db.q(
                    f"WITH per_second AS ("
                    f" SELECT CAST(ts AS INTEGER) sample_second, COUNT(DISTINCT {identity_key}) n"
                    f" FROM events WHERE {' AND '.join(base_where)} AND track_id IS NOT NULL"
                    f" GROUP BY CAST(ts AS INTEGER))"
                    f" SELECT CAST(sample_second / ? AS INTEGER) bucket_id, MAX(n) n"
                    f" FROM per_second GROUP BY bucket_id",
                    [*base_args, bucket_s],
                )
                active_by_bucket = {row["bucket_id"]: row["n"] for row in rows}
        rows = []
        for b in range(b0, b1 + 1):
            bucket_since, bucket_until = b * bucket_s, (b + 1) * bucket_s
            bucket_where = ["ts>=?", "ts<?"]
            bucket_args = [bucket_since, bucket_until]
            row = {"t": bucket_since}
            if "observations" in q.measures:
                row["observations"] = count_rows(bucket_where, bucket_args, distinct=False)
            if "distinct_entities" in q.measures:
                row["distinct_entities"] = count_rows(bucket_where, bucket_args, distinct=True)
            if "active_entities" in q.measures:
                row["active_entities"] = active_by_bucket.get(b, 0)
            rows.append(row)
        return {"shape": "timeseries", "dimensions": ["t"], "measures": q.measures, "rows": rows,
               "metadata": {"bucket_s": bucket_s,
                            "active_entity_semantics": "peak simultaneous scoped tracks per one-second sample"}}
    return {"shape": "scalar", "dimensions": [], "measures": q.measures, "rows": [summarize_scalar()], "metadata": {}}


# ---------------------------------------------------------------------------
# measurement subject
# ---------------------------------------------------------------------------

def _query_measurement(q: QueryIn, since: float, until: float) -> dict:
    filters, grouping = q.filters, q.grouping
    requested_names = filters.get("measurement_names") or [
        r["name"] for r in db.q(
            "SELECT DISTINCT name FROM events WHERE event_type='measurement' AND name IS NOT NULL"
            " AND ts BETWEEN ? AND ?", (since, until))
    ]

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

    # A name with zero matching rows in range never gets a row -- whether it came
    # from an explicit (possibly stale or mistyped) measurement_names filter or
    # the implicit "every distinct name in range" default below. Synthesizing a
    # null-valued placeholder row for a series with no data would be
    # indistinguishable from a real series that is merely idle right now.
    series_by_name = {name: filtered_series(name) for name in requested_names}
    names = [name for name in requested_names if series_by_name[name]]
    if not names:
        return {"shape": "scalar" if not grouping.primary else "timeseries", "dimensions": [],
               "measures": q.measures, "rows": [], "metadata": {"warnings": ["no measurement series matched"]}}

    def aggregate_for(name):
        agg = derive.aggregate_measurement(series_by_name[name])
        return {m: agg[m] for m in q.measures}

    if grouping.primary == "time":
        bucket_s = _bucket_seconds(grouping.bucket, since, until)
        b0, b1 = int(since // bucket_s), int(until // bucket_s)
        rows = []
        for name in names:
            buckets: dict = defaultdict(list)
            for r in series_by_name[name]:
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
    handler = {"detection": _query_detection, "measurement": _query_measurement,
               "state": _query_state, "fused_entity": _query_fused_entity}[q.subject]
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
        "multiview_groups": db.q("SELECT id, name FROM multiview_groups WHERE enabled=1 ORDER BY id"),
        "labels": [r["label"] for r in db.q(
            "SELECT DISTINCT label FROM events WHERE label IS NOT NULL AND label!='' ORDER BY label")],
        "entity_types": [r["entity_type"] for r in db.q(
            "SELECT DISTINCT entity_type FROM events WHERE event_type='detection'"
            " AND entity_type IS NOT NULL AND entity_type!='' ORDER BY entity_type")],
        "measurement_names": [r["name"] for r in db.q(
            "SELECT DISTINCT name FROM events WHERE event_type='measurement' AND name IS NOT NULL ORDER BY name")],
        "state_names": [r["name"] for r in db.q(
            "SELECT DISTINCT name FROM events WHERE event_type='state' AND name IS NOT NULL ORDER BY name")],
        "attribute_keys": sorted({
            k for r in db.q("SELECT attributes FROM events WHERE attributes!='{}' ORDER BY id DESC LIMIT 2000")
            for k in db.jload(r["attributes"], {})
        }),
    }
