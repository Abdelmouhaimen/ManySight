"""Persist complete processed samples and their source-local current entities.

Raw observations remain append-only.  These bounded tables are deterministic read
models: a completion marker commits a sample only when the number of matching
detections equals the marker value.  Missing frames affect freshness, never scene
contents.

Two equivalent commit paths exist:

* `_materialize_from_batch` — the fast path taken by a canonical DetectionSample,
  where the whole sample arrived in one batch and no rows for that sample existed
  before.  The enriched rows are already in memory with the row ids SQLite just
  assigned, so nothing is re-read.
* `materialize_sample` — the general path, which re-reads the marker and its
  detections from `events`.  It still serves marker-first or detection-first
  delivery split across batches, and rebuild/recovery.

Both apply the same completion rule, the same monotonicity rule, and write the
same rows; `tests/test_complete_samples.py` asserts their equivalence.
"""
from __future__ import annotations

from .. import db

FRAME_COUNT_NAME = "detection_frame_count"


def sample_key(sample_id: str | None, timestamp: float) -> str:
    return f"id:{sample_id}" if sample_id else f"ts:{timestamp!r}"


def _sample_predicate(sample_id: str | None) -> tuple[str, list]:
    if sample_id:
        return "sample_id=?", [sample_id]
    return "sample_id IS NULL AND ts=?", []


def _affected_sort_key(value: tuple[int, str | None, float, str]) -> tuple[float, int, str, str]:
    source_id, sample_id, timestamp, entity_type = value
    return timestamp, source_id, entity_type, sample_id or ""


def materialize_affected(enriched: list[dict],
                         self_contained: frozenset[tuple[int, str]] = frozenset()) -> list[dict]:
    """Attempt to commit every detection sample touched by an ingestion batch.

    `self_contained` names `(source_id, sample_id)` pairs the caller has proven
    had no rows in `events` before this batch.  Those commit directly from the
    enriched rows in memory; everything else re-reads, so marker-first or
    detection-first delivery across separate batches still works.  A sample with
    too few or too many detections is retained as raw evidence but never replaces
    current scene state.
    """
    affected: set[tuple[int, str | None, float, str]] = set()
    in_batch: dict[tuple[int, str | None, float, str], dict] = {}
    for event in enriched:
        if event.get("source_id") is None:
            continue
        if event.get("event_type") == "detection":
            entity_type = event.get("entity_type")
            role = "detections"
        elif event.get("event_type") == "measurement" and event.get("name") == FRAME_COUNT_NAME:
            entity_type = event.get("label")
            role = "marker"
        else:
            continue
        if not entity_type:
            continue
        key = (event["source_id"], event.get("sample_id"), event["ts"], entity_type)
        affected.add(key)
        member = in_batch.setdefault(key, {"marker": None, "detections": []})
        if role == "marker":
            member["marker"] = event
        else:
            member["detections"].append(event)

    committed = []
    con = db.active_connection()
    owned = con is None
    if owned:
        con = db.pooled_connection()
    try:
        for key in sorted(affected, key=_affected_sort_key):
            source_id, sid, timestamp, entity_type = key
            if sid is not None and (source_id, sid) in self_contained:
                frame = _materialize_from_batch(con, source_id, entity_type, sid, in_batch[key])
            else:
                frame = materialize_sample(source_id, entity_type, sid, timestamp, connection=con)
            if frame:
                committed.append(frame)
        if owned:
            con.commit()
        return committed
    except Exception:
        if owned:
            con.rollback()
        raise


def _supersedes_current(con, source_id: int, entity_type: str,
                        marker_ts: float, marker_event_id: int) -> bool:
    current = con.execute(
        "SELECT ts, marker_event_id FROM source_current_samples "
        "WHERE source_id=? AND entity_type=?",
        (source_id, entity_type),
    ).fetchone()
    return not (current and (marker_ts, marker_event_id) < (current["ts"], current["marker_event_id"]))


def _write_current_sample(con, source_id: int, entity_type: str, sid: str | None, key: str,
                          marker_ts: float, expected: int, marker_event_id: int,
                          marker_observation_id: str | None, completed_at: float,
                          detections: list[tuple]) -> None:
    con.execute(
        "INSERT INTO source_current_samples "
        "(source_id,entity_type,sample_id,sample_key,ts,expected_count,marker_event_id,"
        " marker_observation_id,completed_at) VALUES (?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(source_id,entity_type) DO UPDATE SET sample_id=excluded.sample_id,"
        " sample_key=excluded.sample_key,ts=excluded.ts,expected_count=excluded.expected_count,"
        " marker_event_id=excluded.marker_event_id,marker_observation_id=excluded.marker_observation_id,"
        " completed_at=excluded.completed_at",
        (source_id, entity_type, sid, key, marker_ts, expected, marker_event_id,
         marker_observation_id, completed_at),
    )
    con.execute(
        "DELETE FROM source_current_entities WHERE source_id=? AND entity_type=?",
        (source_id, entity_type),
    )
    con.executemany(
        "INSERT INTO source_current_entities "
        "(source_id,entity_type,sample_key,event_id,local_entity_id,worker_id,x_map,y_map,"
        " zone_id,confidence,ts) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        detections,
    )


def _materialize_from_batch(con, source_id: int, entity_type: str, sid: str,
                            member: dict) -> dict | None:
    """Commit a complete sample straight from the rows just inserted.

    Equivalent to `materialize_sample` for a sample whose marker and detections
    all arrived in one batch with no earlier rows: same completion rule, same
    monotonicity rule, same written columns — without re-reading `events`.
    """
    marker = member.get("marker")
    if marker is None or marker.get("id") is None:
        return None
    detections = member["detections"]
    expected = int(marker["value"])
    if expected < 0 or len(detections) != expected:
        return None
    if not _supersedes_current(con, source_id, entity_type, marker["ts"], marker["id"]):
        return None
    key = sample_key(sid, marker["ts"])
    _write_current_sample(
        con, source_id, entity_type, sid, key, marker["ts"], expected, marker["id"],
        marker.get("observation_id"), marker.get("created_at") or db.now(),
        [(source_id, entity_type, key, row["id"], row.get("track_id"), row.get("worker_id"),
          row.get("x_map"), row.get("y_map"), row.get("zone_id"), row.get("confidence"),
          row["ts"]) for row in detections],
    )
    return {
        "source_id": source_id,
        "entity_type": entity_type,
        "sample_id": sid,
        "sample_key": key,
        "timestamp": marker["ts"],
        "expected_count": expected,
        "marker_event_id": marker["id"],
        "source_frame_index": (marker.get("attributes") or {}).get("source_frame_index"),
    }


def materialize_sample(source_id: int, entity_type: str, sid: str | None,
                       timestamp: float, connection=None) -> dict | None:
    predicate, predicate_args = _sample_predicate(sid)
    if sid is None:
        predicate_args.append(timestamp)
    con = connection or db.active_connection()
    owned = con is None
    if owned:
        con = db.pooled_connection()
    try:
        marker = con.execute(
            "SELECT * FROM events WHERE source_id=? AND event_type='measurement' "
            "AND name=? AND label=? AND space_revision_id=? AND " + predicate + " ORDER BY id DESC LIMIT 1",
            (source_id, FRAME_COUNT_NAME, entity_type, db.current_space_revision_id(), *predicate_args),
        ).fetchone()
        if marker is None:
            return None
        marker = dict(marker)
        detection_predicate, detection_args = _sample_predicate(sid)
        if sid is None:
            detection_args.append(timestamp)
        detections = [dict(row) for row in con.execute(
            "SELECT * FROM events WHERE source_id=? AND event_type='detection' "
            "AND entity_type=? AND space_revision_id=? AND " + detection_predicate + " ORDER BY id",
            (source_id, entity_type, db.current_space_revision_id(), *detection_args),
        ).fetchall()]
        expected = int(marker["value"])
        if expected < 0 or len(detections) != expected:
            return None

        key = sample_key(sid, marker["ts"])
        if not _supersedes_current(con, source_id, entity_type, marker["ts"], marker["id"]):
            return None

        _write_current_sample(
            con, source_id, entity_type, sid, key, marker["ts"], expected, marker["id"],
            marker.get("observation_id"), marker.get("created_at") or db.now(),
            [(source_id, entity_type, key, row["id"], row.get("track_id"), row.get("worker_id"),
              row.get("x_map"), row.get("y_map"), row.get("zone_id"), row.get("confidence"),
              row["ts"]) for row in detections],
        )
        if owned:
            con.commit()
        return {
            "source_id": source_id,
            "entity_type": entity_type,
            "sample_id": sid,
            "sample_key": key,
            "timestamp": marker["ts"],
            "expected_count": expected,
            "marker_event_id": marker["id"],
            "source_frame_index": db.jload(marker.get("attributes"), {}).get("source_frame_index"),
        }
    except Exception:
        if owned:
            con.rollback()
        raise


def rebuild_from_history() -> int:
    """Rebuild current samples after migration or for explicit recovery."""
    markers = db.q(
        "WITH ranked AS (SELECT *, ROW_NUMBER() OVER (PARTITION BY source_id,label "
        "ORDER BY ts DESC,id DESC) rn FROM events WHERE event_type='measurement' "
        "AND name=? AND space_revision_id=?) SELECT * FROM ranked WHERE rn=1",
        (FRAME_COUNT_NAME, db.current_space_revision_id()),
    )
    count = 0
    for marker in markers:
        if materialize_sample(marker["source_id"], marker["label"], marker.get("sample_id"), marker["ts"]):
            count += 1
    return count
