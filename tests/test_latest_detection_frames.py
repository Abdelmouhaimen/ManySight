"""Latest completed processed-frame read model regressions."""

import pytest

from helpers import make_detection, make_measurement
from server import db


def frame_batch(source_id, ts, entity_ids, prefix="frame", frame_index=None):
    observations = [
        make_detection(
            source_id,
            f"{prefix}-detection-{entity_id}",
            ts,
            entity_id=entity_id,
            point_px=(100 + index * 100, 200),
        )
        for index, entity_id in enumerate(entity_ids)
    ]
    marker_attributes = {} if frame_index is None else {"source_frame_index": frame_index}
    observations.append(make_measurement(
        source_id,
        f"{prefix}-count",
        ts,
        "detection_frame_count",
        len(entity_ids),
        label="person",
        unit="tracks",
        attributes=marker_attributes,
    ))
    return {"observations": observations}


def latest_frames(client, source_id=None):
    params = {"entity_type": "person"}
    if source_id is not None:
        params["source_id"] = source_id
    response = client.get("/api/v1/observations/latest-frames", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_latest_frame_returns_all_detections_and_projected_coordinates(client, calibrated_source):
    response = client.post(
        "/api/v1/observations/batch",
        json=frame_batch(calibrated_source, 1000.125, ["A", "B"]),
    )
    assert response.status_code == 200, response.text

    result = latest_frames(client, calibrated_source)
    frame = result["frames"][0]
    assert frame["timestamp"] == 1000.125
    assert frame["expected_count"] == 2
    assert [row["entity_id"] for row in frame["detections"]] == ["A", "B"]
    point = frame["detections"][0]["geometry"]["point_map"]
    assert point["x"] == pytest.approx(1.0)
    assert point["y"] == pytest.approx(2.0)


def test_new_zero_frame_clears_previous_frame(client, calibrated_source):
    client.post("/api/v1/observations/batch", json=frame_batch(calibrated_source, 1000.0, ["A", "B"], "t0"))
    client.post("/api/v1/observations/batch", json=frame_batch(calibrated_source, 1001.0, [], "t1"))

    frame = latest_frames(client, calibrated_source)["frames"][0]
    assert frame["timestamp"] == 1001.0
    assert frame["expected_count"] == 0
    assert frame["detections"] == []


def test_frame_index_comes_from_completion_marker_even_when_frame_is_empty(client, calibrated_source):
    client.post(
        "/api/v1/observations/batch",
        json=frame_batch(calibrated_source, 1001.0, [], "empty-indexed", frame_index=30),
    )

    frame = latest_frames(client, calibrated_source)["frames"][0]
    assert frame["source_frame_index"] == 30
    assert frame["detections"] == []


def test_new_nonempty_frame_replaces_instead_of_merging(client, calibrated_source):
    client.post("/api/v1/observations/batch", json=frame_batch(calibrated_source, 1000.0, ["A", "B"], "t0"))
    client.post("/api/v1/observations/batch", json=frame_batch(calibrated_source, 1001.0, ["C"], "t1"))

    frame = latest_frames(client, calibrated_source)["frames"][0]
    assert frame["expected_count"] == 1
    assert [row["entity_id"] for row in frame["detections"]] == ["C"]


def test_scene_persists_when_source_freshness_becomes_stale(client, calibrated_source, monkeypatch):
    ingested_at = db.now()
    client.post("/api/v1/observations/batch", json=frame_batch(calibrated_source, 1000.0, ["A", "B"], "t0"))
    monkeypatch.setattr(db, "now", lambda: ingested_at + 60.0)

    frame = latest_frames(client, calibrated_source)["frames"][0]
    assert [row["entity_id"] for row in frame["detections"]] == ["A", "B"]
    assert frame["stale"] is True
    assert frame["source_age_s"] >= 59.0


def test_sources_have_independent_latest_frames(client, calibrated_source):
    second = client.post("/api/v1/sources", json={"name": "Camera B", "kind": "webcam"}).json()["id"]
    client.post("/api/v1/observations/batch", json=frame_batch(calibrated_source, 1000.0, ["A", "B"], "a0"))
    client.post("/api/v1/observations/batch", json=frame_batch(second, 1000.0, ["C"], "b0"))
    client.post("/api/v1/observations/batch", json=frame_batch(calibrated_source, 1001.0, [], "a1"))

    frames = {frame["source_id"]: frame for frame in latest_frames(client)["frames"]}
    assert frames[calibrated_source]["detections"] == []
    assert [row["entity_id"] for row in frames[second]["detections"]] == ["C"]


def test_batch_sse_preserves_detection_then_frame_marker_order(client, calibrated_source, monkeypatch):
    from server.services.sse import broker

    published = []
    # Ingestion skips building SSE payloads when nobody is listening, so a test
    # about what subscribers receive has to say that someone is.
    monkeypatch.setattr(broker, "has_subscribers", lambda: True)
    monkeypatch.setattr(broker, "publish", lambda event, data: published.append((event, data)))
    payload = frame_batch(calibrated_source, 1000.0, ["A", "B"], "ordered")
    response = client.post("/api/v1/observations/batch", json=payload)
    assert response.status_code == 200, response.text

    created_ids = [
        data["observation_id"] for event, data in published if event == "observation.created"
    ]
    assert created_ids == ["ordered-detection-A", "ordered-detection-B", "ordered-count"]


def test_late_detection_after_completion_marker_does_not_mutate_committed_frame(client, calibrated_source):
    client.post("/api/v1/observations/batch", json=frame_batch(calibrated_source, 1000.0, [], "committed"))
    late = make_detection(
        calibrated_source,
        "late-detection",
        1000.0,
        entity_id="late",
        point_px=(100, 200),
    )
    client.post("/api/v1/observations/batch", json={"observations": [late]})

    frame = latest_frames(client, calibrated_source)["frames"][0]
    assert frame["expected_count"] == 0
    assert frame["detections"] == []
