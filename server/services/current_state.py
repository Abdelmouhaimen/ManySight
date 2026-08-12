"""Persist complete processed samples and their source-local current entities.

Raw observations remain append-only.  These bounded tables are deterministic read
models: a completion marker commits a sample only when the number of matching
detections equals the marker value.  Missing frames affect freshness, never scene
contents.
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


def materialize_affected(enriched: list[dict]) -> list[dict]:
    """Attempt to commit every detection sample touched by an ingestion batch.

    This supports marker-first or detection-first delivery across separate batches:
    every row causes its sample to be rechecked.  A sample with too few or too many
    detections is retained as raw evidence but never replaces current scene state.
    """
    affected: set[tuple[int, str | None, float, str]] = set()
    for event in enriched:
        if event.get("source_id") is None:
            continue
        if event.get("event_type") == "detection":
            entity_type = event.get("entity_type")
        elif event.get("event_type") == "measurement" and event.get("name") == FRAME_COUNT_NAME:
            entity_type = event.get("label")
        else:
            continue
        if entity_type:
            affected.add((event["source_id"], event.get("sample_id"), event["ts"], entity_type))

    committed = []
    for source_id, sid, timestamp, entity_type in sorted(affected, key=lambda value: value[2]):
        frame = materialize_sample(source_id, entity_type, sid, timestamp)
        if frame:
            committed.append(frame)
    return committed


def materialize_sample(source_id: int, entity_type: str, sid: str | None,
                       timestamp: float) -> dict | None:
    predicate, predicate_args = _sample_predicate(sid)
    if sid is None:
        predicate_args.append(timestamp)
    con = db.connect()
    try:
        marker = con.execute(
            "SELECT * FROM events WHERE source_id=? AND event_type='measurement' "
            "AND name=? AND label=? AND " + predicate + " ORDER BY id DESC LIMIT 1",
            (source_id, FRAME_COUNT_NAME, entity_type, *predicate_args),
        ).fetchone()
        if marker is None:
            return None
        marker = dict(marker)
        detection_predicate, detection_args = _sample_predicate(sid)
        if sid is None:
            detection_args.append(timestamp)
        detections = [dict(row) for row in con.execute(
            "SELECT * FROM events WHERE source_id=? AND event_type='detection' "
            "AND entity_type=? AND " + detection_predicate + " ORDER BY id",
            (source_id, entity_type, *detection_args),
        ).fetchall()]
        expected = int(marker["value"])
        if expected < 0 or len(detections) != expected:
            return None

        key = sample_key(sid, marker["ts"])
        current = con.execute(
            "SELECT ts, marker_event_id FROM source_current_samples "
            "WHERE source_id=? AND entity_type=?",
            (source_id, entity_type),
        ).fetchone()
        if current and (marker["ts"], marker["id"]) < (current["ts"], current["marker_event_id"]):
            return None

        con.execute(
            "INSERT INTO source_current_samples "
            "(source_id,entity_type,sample_id,sample_key,ts,expected_count,marker_event_id,"
            " marker_observation_id,completed_at) VALUES (?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(source_id,entity_type) DO UPDATE SET sample_id=excluded.sample_id,"
            " sample_key=excluded.sample_key,ts=excluded.ts,expected_count=excluded.expected_count,"
            " marker_event_id=excluded.marker_event_id,marker_observation_id=excluded.marker_observation_id,"
            " completed_at=excluded.completed_at",
            (source_id, entity_type, sid, key, marker["ts"], expected, marker["id"],
             marker.get("observation_id"), marker.get("created_at") or db.now()),
        )
        con.execute(
            "DELETE FROM source_current_entities WHERE source_id=? AND entity_type=?",
            (source_id, entity_type),
        )
        con.executemany(
            "INSERT INTO source_current_entities "
            "(source_id,entity_type,sample_key,event_id,local_entity_id,worker_id,x_map,y_map,"
            " zone_id,confidence,ts) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [(source_id, entity_type, key, row["id"], row.get("track_id"), row.get("worker_id"),
              row.get("x_map"), row.get("y_map"), row.get("zone_id"), row.get("confidence"),
              row["ts"]) for row in detections],
        )
        con.commit()
        return {
            "source_id": source_id,
            "entity_type": entity_type,
            "sample_id": sid,
            "sample_key": key,
            "timestamp": marker["ts"],
            "expected_count": expected,
            "marker_event_id": marker["id"],
        }
    finally:
        con.close()


def rebuild_from_history() -> int:
    """Rebuild current samples after migration or for explicit recovery."""
    markers = db.q(
        "WITH ranked AS (SELECT *, ROW_NUMBER() OVER (PARTITION BY source_id,label "
        "ORDER BY ts DESC,id DESC) rn FROM events WHERE event_type='measurement' "
        "AND name=?) SELECT * FROM ranked WHERE rn=1",
        (FRAME_COUNT_NAME,),
    )
    count = 0
    for marker in markers:
        if materialize_sample(marker["source_id"], marker["label"], marker.get("sample_id"), marker["ts"]):
            count += 1
    return count
