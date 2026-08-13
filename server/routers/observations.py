"""Observation ingestion and querying — the current worker contract.

Workers submit only three observation kinds: `detection`, `measurement`, `state`.
StoreLens derives everything else (zones, visits, dwell, occupancy, movement, state
transitions and durations, aggregations, analytics, alerts) from these raw rows —
see services/derive.py. Workers must never resolve zones or send zone_id/zone,
and must never submit the legacy derived kinds (zone_enter, zone_exit, zone_dwell,
state_change, count); those are rejected with a `legacy_derived_observation` error
so a client that tries the old contract gets an explicit, actionable message
instead of a silently misinterpreted row.

Ingestion shares one enrichment implementation with the legacy /events endpoint
(services/enrich.py) — the projection/zone-assignment pipeline is not duplicated.
"""
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import db
from ..services import alert_engine, current_state, derive, enrich

router = APIRouter(tags=["observations"])
LATEST_LOOKBACK_S = 24 * 3600.0
SOURCE_FRESH_S = 30.0

REQUIRED_BY_KIND = {
    "detection": [],
    "measurement": ["name", "value"],
    "state": ["name", "label"],
}


class GeometryIn(BaseModel):
    point_px: list[float] | None = None          # [x, y] pixels
    bbox_px: list[float] | None = None            # [x0, y0, x1, y1] pixels (corner form)
    keypoints_px: dict[str, list[float]] | None = None  # {name: [x, y]}
    mask: dict | str | None = None
    point_map: dict | None = None                 # {x, y} map metres — trusted non-camera producers only


class ObservationIn(BaseModel):
    schema_version: int = 2
    observation_id: str
    sample_id: str | None = None
    kind: str
    timestamp: float | str
    source_id: int
    worker_id: int | None = None
    job_id: int | None = None
    confidence: float | None = None
    label: str | None = None
    name: str | None = None
    value: float | None = None
    value_kind: str = "gauge"
    unit: str | None = None
    attributes: dict = {}
    info: dict = {}
    geometry: GeometryIn | None = None
    entity_id: str | None = None
    entity_type: str | None = None
    identity_scope: str = "worker_run"
    identity_model_version: str | None = None
    zone_id: int | None = None
    zone: str | None = None
    projection_surface_id: int | None = None
    zone_view_id: int | None = None


class ObservationBatch(BaseModel):
    job_id: int | None = None
    observations: list[ObservationIn]


def _parse_ts(ts) -> float:
    if isinstance(ts, (int, float)):
        return float(ts)
    return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()


def _normalize_geometry(geometry: GeometryIn | None) -> dict:
    if geometry is None:
        return {}
    out = {}
    if geometry.point_px is not None:
        if len(geometry.point_px) != 2:
            raise ValueError("geometry.point_px must be [x, y]")
        out["point_px"] = {"x": geometry.point_px[0], "y": geometry.point_px[1]}
    if geometry.bbox_px is not None:
        if len(geometry.bbox_px) != 4:
            raise ValueError("geometry.bbox_px must be [x0, y0, x1, y1]")
        x0, y0, x1, y1 = geometry.bbox_px
        out["bbox"] = [x0, y0, x1 - x0, y1 - y0]
    if geometry.keypoints_px is not None:
        out["keypoints"] = [
            {"name": name, "x": xy[0], "y": xy[1], "confidence": 1.0}
            for name, xy in geometry.keypoints_px.items() if len(xy) == 2
        ]
    if geometry.mask is not None:
        out["mask"] = geometry.mask
    if geometry.point_map is not None:
        out["point_map"] = geometry.point_map
    return out


def _to_enrich_dict(ob: ObservationIn, ts: float, fallback_job_id: int | None) -> dict:
    ev = {
        "job_id": ob.job_id if ob.job_id is not None else fallback_job_id,
        "source_id": ob.source_id,
        "event_type": ob.kind,
        "track_id": ob.entity_id,
        "zone_id": None,       # workers never resolve zones under this contract
        "zone": None,
        "value": ob.value,
        "label": ob.label,
        "point_kind": None,
        "projection_surface_id": ob.projection_surface_id,
        "zone_view_id": ob.zone_view_id,
        "attributes": {**ob.attributes, **ob.info} if ob.info else ob.attributes,
        "schema_version": ob.schema_version,
        "observation_id": ob.observation_id,
        "sample_id": ob.sample_id,
        "worker_id": ob.worker_id,
        "name": ob.name,
        "entity_type": ob.entity_type,
        "value_kind": ob.value_kind,
        "unit": ob.unit,
        "confidence": ob.confidence,
        "identity_scope": ob.identity_scope,
        "identity_model_version": ob.identity_model_version,
    }
    ev.update(_normalize_geometry(ob.geometry))
    return ev


@router.get("/observations/contract", summary="Get the current worker observation contract")
def observation_contract():
    """Machine-readable summary of the current worker contract: the three kinds,
    required fields per kind, and what workers must never send. Mirrors AGENTS.md
    and the storelens-platform skill so an agent can self-check a payload."""
    return {
        "schema_version": 2,
        "kinds": sorted(enrich.OBSERVATION_KINDS),
        "required_fields": {"common": ["schema_version", "observation_id", "kind", "timestamp", "source_id"],
                            **REQUIRED_BY_KIND},
        "optional_common_fields": ["sample_id", "worker_id", "job_id", "confidence", "label", "attributes",
                                   "geometry", "entity_id", "identity_scope", "identity_model_version"],
        "forbidden": {
            "top_level_fields": ["zone_id", "zone"],
            "kinds": sorted(enrich.LEGACY_DERIVED_KINDS),
        },
        "identity_scopes": ["worker_run", "source", "workspace"],
        "value_kinds": ["gauge", "delta", "cumulative"],
        "processed_detection_frame": {
            "kind": "measurement",
            "name": "detection_frame_count",
            "label": "<entity_type>",
            "value": "number of matching detections in this processed frame, including 0",
            "timestamp": "same timestamp as every detection emitted from the frame",
            "sample_id": "same opaque source-local sample id as every detection emitted from the frame",
            "ordering": "append after that frame's detections, then flush the batch",
            "required_when_zero": True,
            "purpose": "commits the latest completed processed frame and provides its instantaneous count",
        },
        "representative_point_precedence": [
            "explicit geometry.point_px",
            "foot/ankle keypoints (averaged)",
            "bottom-center of geometry.bbox_px",
            "left empty when only a mask is present",
        ],
    }


@router.post("/observations/batch", summary="Submit schema-v2 raw observations")
async def submit_observations(batch: ObservationBatch):
    if not batch.observations:
        return {"accepted": 0, "duplicates": 0, "rejected": []}
    if len(batch.observations) > 5000:
        raise HTTPException(413, "batch too large — send at most 5000 observations per request")
    if batch.job_id is not None and not db.q1("SELECT id FROM jobs WHERE id=?", (batch.job_id,)):
        raise HTTPException(404, f"job {batch.job_id} not found — register a job first")

    source_ids = {ob.source_id for ob in batch.observations}
    known_sources = {
        row["id"] for row in db.q(
            f"SELECT id FROM sources WHERE id IN ({','.join('?' for _ in source_ids)})",
            tuple(source_ids),
        )
    } if source_ids else set()
    missing_sources = sorted(source_ids - known_sources)
    if missing_sources:
        raise HTTPException(404, f"unknown source ids {missing_sources} — create sources first")

    existing_ids = {
        r["observation_id"] for r in db.q(
            f"SELECT observation_id FROM events WHERE observation_id IN "
            f"({','.join('?' for _ in batch.observations)})",
            tuple(ob.observation_id for ob in batch.observations),
        )
    } if batch.observations else set()

    # Explicit sample IDs are source-local atomic commit keys.  They may arrive
    # over several batches, but every member must retain one exact timestamp and
    # a sample may have only one completion marker per entity type.
    explicit_sample_times: dict[tuple[int, str], set[float]] = {}
    parsed_timestamps: dict[int, float] = {}
    for index, ob in enumerate(batch.observations):
        try:
            parsed = _parse_ts(ob.timestamp)
        except (ValueError, TypeError):
            continue
        parsed_timestamps[index] = parsed
        if ob.sample_id:
            explicit_sample_times.setdefault((ob.source_id, ob.sample_id), set()).add(parsed)
    invalid_sample_keys = {
        key for key, timestamps in explicit_sample_times.items() if len(timestamps) != 1
    }
    for key, timestamps in explicit_sample_times.items():
        if key in invalid_sample_keys:
            continue
        existing = db.q(
            "SELECT DISTINCT ts FROM events WHERE source_id=? AND sample_id=? "
            "AND space_revision_id=? LIMIT 2", (*key, db.current_space_revision_id()),
        )
        if any(float(row["ts"]) != next(iter(timestamps)) for row in existing):
            invalid_sample_keys.add(key)
    marker_keys_seen: set[tuple[int, str, str]] = set()

    context = enrich.load_geometry_context()
    zone_by_id = {z["id"]: z for z in context[0]}
    rejected, enriched, rows = [], [], []
    seen_in_batch = set()
    duplicates = 0
    for index, ob in enumerate(batch.observations):
        if ob.observation_id in existing_ids or ob.observation_id in seen_in_batch:
            duplicates += 1
            continue
        seen_in_batch.add(ob.observation_id)
        explicit_key = (ob.source_id, ob.sample_id) if ob.sample_id else None
        if explicit_key in invalid_sample_keys:
            rejected.append({
                "index": index, "observation_id": ob.observation_id,
                "error": "sample_timestamp_mismatch",
                "message": "All observations sharing a source_id and sample_id must use one exact timestamp.",
            })
            continue
        if ob.kind == "measurement" and ob.name == current_state.FRAME_COUNT_NAME and ob.sample_id:
            marker_key = (ob.source_id, ob.sample_id, ob.label or "")
            duplicate_marker = marker_key in marker_keys_seen or db.q1(
                "SELECT id FROM events WHERE source_id=? AND sample_id=? AND event_type='measurement' "
                "AND name=? AND COALESCE(label,'')=? AND space_revision_id=? LIMIT 1",
                (ob.source_id, ob.sample_id, current_state.FRAME_COUNT_NAME, ob.label or "",
                 db.current_space_revision_id()),
            )
            if duplicate_marker:
                rejected.append({
                    "index": index, "observation_id": ob.observation_id,
                    "error": "duplicate_completion_marker",
                    "message": "A detection sample accepts exactly one completion marker per entity type.",
                })
                continue
            marker_keys_seen.add(marker_key)
        if ob.kind in enrich.LEGACY_DERIVED_KINDS:
            rejected.append({
                "index": index, "observation_id": ob.observation_id,
                "error": "legacy_derived_observation",
                "message": (
                    f"Workers must submit detection, measurement, or state observations. "
                    f"'{ob.kind}' is derived by StoreLens, not submitted — see GET /observations/contract."
                ),
            })
            continue
        if ob.kind not in enrich.OBSERVATION_KINDS:
            rejected.append({"index": index, "observation_id": ob.observation_id,
                             "error": "invalid_kind",
                             "message": f"kind must be one of {sorted(enrich.OBSERVATION_KINDS)}"})
            continue
        if ob.zone_id is not None or ob.zone is not None:
            rejected.append({
                "index": index, "observation_id": ob.observation_id,
                "error": "zone_resolution_forbidden",
                "message": "Workers must not resolve zones or send zone_id/zone; StoreLens assigns "
                           "zones from geometry at ingestion.",
            })
            continue
        missing = [f for f in REQUIRED_BY_KIND[ob.kind] if getattr(ob, f) is None]
        if missing:
            rejected.append({"index": index, "observation_id": ob.observation_id,
                             "error": "missing_required_field",
                             "message": f"{ob.kind} observations require {missing}"})
            continue
        try:
            ts = parsed_timestamps.get(index, _parse_ts(ob.timestamp))
        except (ValueError, TypeError):
            rejected.append({"index": index, "observation_id": ob.observation_id,
                             "error": "invalid_timestamp", "message": "timestamp must be epoch seconds or ISO-8601"})
            continue
        ev_dict = _to_enrich_dict(ob, ts, batch.job_id)
        try:
            enrich.validate_shape(ev_dict)
            e = enrich.enrich_one(ev_dict, context, zone_by_id)
        except (ValueError, LookupError) as exc:
            rejected.append({"index": index, "observation_id": ob.observation_id,
                             "error": "invalid_observation", "message": str(exc)})
            continue
        e["ts"] = ts
        enriched.append(e)
        rows.append(enrich.row_tuple(e, ts, db.now()))

    if rows:
        db.exmany(enrich.INSERT_SQL, rows)
        enrich.update_counters(enriched, batch.job_id)
    completed_samples = current_state.materialize_affected(enriched) if enriched else []

    # Fusion is deliberately downstream of complete-source-sample materialization.
    # Import lazily so deployments that never configure multiview retain the same
    # ingestion dependency surface.
    if completed_samples:
        from ..services import multiview
        for sample in completed_samples:
            multiview.process_completed_sample(sample)

    zone_names = {z["id"]: z["name"] for z in context[0]}
    alerts = alert_engine.evaluate_batch(enriched, zone_names) if enriched else []
    enrich.publish_batch(enriched, alerts, zone_names, completed_samples=completed_samples)

    return {
        "accepted": len(rows), "duplicates": duplicates, "rejected": rejected,
        "alerts": len(alerts), "completed_samples": len(completed_samples),
    }


def _serialize_observation(r: dict, zone_names: dict) -> dict:
    return {
        "id": r["id"], "schema_version": r.get("schema_version", 1),
        "observation_id": r.get("observation_id"), "kind": r["event_type"],
        "sample_id": r.get("sample_id"),
        "space_revision_id": r.get("space_revision_id", 1),
        "current_space_revision": int(r.get("space_revision_id", 1)) == db.current_space_revision_id(),
        "ts": r["ts"], "source_id": r["source_id"], "job_id": r["job_id"],
        "worker_id": r.get("worker_id"), "entity_id": r["track_id"], "entity_type": r.get("entity_type"),
        "identity_scope": r.get("identity_scope"), "identity_model_version": r.get("identity_model_version"),
        "name": r.get("name"), "label": r["label"], "value": r["value"], "value_kind": r.get("value_kind"),
        "unit": r.get("unit"), "confidence": r.get("confidence"),
        "attributes": db.jload(r["attributes"], {}),
        "geometry": {
            "point_px": None if r["x_px"] is None else {"x": r["x_px"], "y": r["y_px"]},
            "point_map": None if r["x_map"] is None else {"x": r["x_map"], "y": r["y_map"]},
            "bbox": db.jload(r.get("bbox_json"), None),
            "keypoints": db.jload(r.get("keypoints_json"), None),
            "mask": db.jload(r.get("mask_json"), None),
            "point_kind": r.get("point_kind"),
        },
        "zone_id": r["zone_id"], "zone_name": zone_names.get(r["zone_id"]),
        "zone_assignment_method": r.get("zone_assignment_method"),
        "projection_method": r.get("projection_method"),
        "projection_surface_id": r.get("projection_surface_id"),
        "zone_view_id": r.get("zone_view_id"),
        "revisions": {
            "zone": r.get("zone_revision"), "calibration": r.get("calibration_revision"),
            "surface": r.get("surface_revision"), "zone_view": r.get("zone_view_revision"),
        },
        "created_at": r["created_at"],
    }


@router.get("/observations", summary="List stored observations")
def list_observations(since: float | None = None, until: float | None = None,
                      kind: str | None = None, source_id: int | None = None,
                      worker_id: int | None = None, entity_id: str | None = None,
                      label: str | None = None, name: str | None = None,
                      zone_id: int | None = None, cursor: str | None = None,
                      space_revision_id: int | None = None,
                      include_previous_space: bool = False,
                      limit: int = 200):
    """Keyset-paginated raw observations (current + legacy kinds). Pass the
    returned `next_cursor` back as `cursor`; `total` counts all matching rows."""
    limit = min(max(1, limit), 5000)
    where, args = ["1=1"], []
    if space_revision_id is not None:
        where.append("space_revision_id=?")
        args.append(space_revision_id)
    elif not include_previous_space:
        where.append("space_revision_id=?")
        args.append(db.current_space_revision_id())
    for clause, val in (("ts>=?", since), ("ts<=?", until), ("event_type=?", kind),
                        ("source_id=?", source_id), ("worker_id=?", worker_id),
                        ("track_id=?", entity_id), ("label=?", label), ("name=?", name),
                        ("zone_id=?", zone_id)):
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
    observations = [_serialize_observation(r, zone_names) for r in rows]
    next_cursor = f"{rows[-1]['ts']!r}:{rows[-1]['id']}" if len(rows) == limit else None
    return {"observations": observations, "count": len(observations), "total": total, "next_cursor": next_cursor}


# --------------------------------------------------------------------------
# Latest-value read models. Raw observations stay append-only; these are
# derived read models computed at query time from the same `events` table
# (no separate materialized/cached copy), so they are always rebuildable and
# never a second source of truth. Kept here because they read the same
# storage this router writes to.
#
# Registered before /observations/{observation_id}: a dynamic single-segment
# path parameter route would otherwise shadow this static path (FastAPI/
# Starlette matches routes in registration order), swallowing every request
# to /observations/latest and failing int-parsing on "latest" as if it were
# an observation_id. Keep any future static /observations/* route above the
# {observation_id} route below for the same reason.
# --------------------------------------------------------------------------

def _current_detections(since: float, now: float, source_id: int | None, zone_id: int | None) -> dict:
    where, args = ["event_type='detection'", "track_id IS NOT NULL", "ts>=?", "space_revision_id=?"], [since, db.current_space_revision_id()]
    if source_id is not None:
        where.append("source_id=?"); args.append(source_id)
    if zone_id is not None:
        where.append("zone_id=?"); args.append(zone_id)
    rows = db.q(
        f"WITH latest AS (SELECT *, ROW_NUMBER() OVER "
        f"(PARTITION BY track_id ORDER BY ts DESC, id DESC) rn FROM events WHERE {' AND '.join(where)})"
        f" SELECT * FROM latest WHERE rn=1 ORDER BY ts DESC", args)
    zone_names = {z["id"]: z["name"] for z in db.q("SELECT id, name FROM zones")}
    entities = []
    for r in rows:
        age = now - r["ts"]
        entities.append({
            "entity_id": r["track_id"], "entity_type": r.get("entity_type"), "label": r["label"],
            "source_id": r["source_id"], "zone_id": r["zone_id"], "zone_name": zone_names.get(r["zone_id"]),
            "last_seen_at": r["ts"], "age_s": round(age, 1), "stale": age > derive.PRESENCE_TIMEOUT_S,
        })
    return {"kind": "detection", "active_count": sum(1 for e in entities if not e["stale"]),
           "entities": entities}


def _current_measurements(since: float, now: float, source_id: int | None, name: str | None) -> dict:
    where, args = ["event_type='measurement'", "name IS NOT NULL", "ts>=?", "space_revision_id=?"], [since, db.current_space_revision_id()]
    if source_id is not None:
        where.append("source_id=?"); args.append(source_id)
    if name is not None:
        where.append("name=?"); args.append(name)
    rows = db.q(
        f"WITH latest AS (SELECT *, ROW_NUMBER() OVER "
        f"(PARTITION BY source_id, name, label, track_id ORDER BY ts DESC, id DESC) rn"
        f" FROM events WHERE {' AND '.join(where)})"
        f" SELECT * FROM latest WHERE rn=1 ORDER BY name, source_id", args)
    zone_names = {z["id"]: z["name"] for z in db.q("SELECT id, name FROM zones")}
    series = []
    for r in rows:
        age = now - r["ts"]
        series.append({
            "source_id": r["source_id"], "name": r["name"], "label": r["label"],
            "entity_id": r["track_id"], "value": r["value"], "unit": r.get("unit"),
            "value_kind": r.get("value_kind") or "gauge", "zone_id": r["zone_id"],
            "zone_name": zone_names.get(r["zone_id"]), "last_seen_at": r["ts"],
            "age_s": round(age, 1), "stale": age > derive.PRESENCE_TIMEOUT_S,
        })
    return {"kind": "measurement", "series": series}


def _current_states(since: float, now: float, source_id: int | None) -> dict:
    zone_names = {z["id"]: z["name"] for z in db.q("SELECT id, name FROM zones")}
    series = []
    for sid, name, entity_id in derive.state_keys(since, now, source_id):
        interval = derive.current_state_interval(sid, name, entity_id, now)
        if not interval:
            continue
        latest_zone = db.q1(
            "SELECT zone_id FROM events WHERE event_type='state' AND source_id=? AND name=?"
            " AND (track_id=? OR (? IS NULL AND track_id IS NULL)) AND space_revision_id=? "
            "ORDER BY ts DESC, id DESC LIMIT 1",
            (sid, name, entity_id, entity_id, db.current_space_revision_id()))
        zone_id = latest_zone["zone_id"] if latest_zone else None
        series.append({
            "source_id": sid, "name": name, "entity_id": entity_id,
            "label": interval["label"], "since_ts": interval["start"],
            "duration_s": round(now - interval["start"], 1),
            "last_seen_at": interval["last_sample_ts"], "stale": interval["stale"],
            "zone_id": zone_id, "zone_name": zone_names.get(zone_id),
        })
    return {"kind": "state", "series": series}


@router.get("/observations/latest", summary="Get current read models derived from observations")
def latest_observations(kind: str | None = None, source_id: int | None = None,
                        zone_id: int | None = None, name: str | None = None,
                        since: float | None = None):
    """Current-value read models: active entities (detection), latest sample per
    series (measurement), and current/duration per series (state). Derived from
    the same append-only `events` table at query time — rebuildable, never a
    second source of truth. Pass `kind` to fetch one read model; omit it for all
    three."""
    now = db.now()
    since = since if since is not None else now - LATEST_LOOKBACK_S
    if kind == "detection":
        return _current_detections(since, now, source_id, zone_id)
    if kind == "measurement":
        return _current_measurements(since, now, source_id, name)
    if kind == "state":
        return _current_states(since, now, source_id)
    if kind is not None:
        raise HTTPException(422, "kind must be one of detection, measurement, state")
    return {
        "detection": _current_detections(since, now, source_id, zone_id),
        "measurement": _current_measurements(since, now, source_id, name),
        "state": _current_states(since, now, source_id),
    }


@router.get("/observations/latest-frames", summary="Get each source's latest completed detection frame")
def latest_detection_frames(entity_type: str = "person", source_id: int | None = None):
    """Return the latest explicitly completed processed frame per source.

    A ``detection_frame_count`` measurement is the completion marker. Detections
    for the frame are selected by the exact ``source_id + timestamp`` key and are
    returned with their stored StoreLens geometry enrichment. Scene contents do
    not expire; ``stale`` reports source freshness independently.
    """
    where = ["entity_type=?"]
    args: list = [entity_type]
    if source_id is not None:
        where.append("source_id=?")
        args.append(source_id)
    markers = db.q(
        f"SELECT * FROM source_current_samples WHERE {' AND '.join(where)} ORDER BY source_id",
        args,
    )
    if not markers:
        current_state.rebuild_from_history()
        markers = db.q(
            f"SELECT * FROM source_current_samples WHERE {' AND '.join(where)} ORDER BY source_id",
            args,
        )
    now = db.now()
    zone_names = {z["id"]: z["name"] for z in db.q("SELECT id, name FROM zones")}
    source_rows = {
        row["id"]: row for row in db.q(
            "SELECT id, last_ingestion_at, last_observation_at FROM sources"
            + (" WHERE id=?" if source_id is not None else ""),
            (source_id,) if source_id is not None else (),
        )
    }
    frames = []
    for marker in markers:
        detections = db.q(
            "SELECT e.* FROM source_current_entities c JOIN events e ON e.id=c.event_id "
            "WHERE c.source_id=? AND c.entity_type=? AND c.sample_key=? ORDER BY e.id",
            (marker["source_id"], entity_type, marker["sample_key"]),
        )
        source = source_rows.get(marker["source_id"], {})
        last_ingestion_at = source.get("last_ingestion_at")
        source_age_s = max(0.0, now - last_ingestion_at) if last_ingestion_at is not None else None
        frame_ingested_at = marker.get("completed_at")
        frames.append({
            "source_id": marker["source_id"],
            "entity_type": entity_type,
            "timestamp": marker["ts"],
            "sample_id": marker.get("sample_id"),
            "sample_key": marker["sample_key"],
            "expected_count": int(marker["expected_count"]),
            "observed_count": len(detections),
            "frame_observation_id": marker.get("marker_observation_id"),
            "frame_ingested_at": frame_ingested_at,
            "frame_age_s": round(max(0.0, now - frame_ingested_at), 1)
            if frame_ingested_at is not None else None,
            "source_last_ingestion_at": last_ingestion_at,
            "source_age_s": round(source_age_s, 1) if source_age_s is not None else None,
            "stale": source_age_s is None or source_age_s > SOURCE_FRESH_S,
            "detections": [_serialize_observation(row, zone_names) for row in detections],
        })
    return {
        "entity_type": entity_type,
        "frame_key": "source_id + sample_id (exact timestamp fallback for legacy workers)",
        "as_of": now,
        "stale_after_s": SOURCE_FRESH_S,
        "frames": frames,
    }


@router.get("/observations/{observation_id}", summary="Get one stored observation")
def get_observation(observation_id: int):
    row = db.q1("SELECT * FROM events WHERE id=?", (observation_id,))
    if not row:
        raise HTTPException(404, "observation not found")
    zone_names = {z["id"]: z["name"] for z in db.q("SELECT id, name FROM zones")}
    return _serialize_observation(row, zone_names)
