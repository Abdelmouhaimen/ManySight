"""Preferred atomic detection-sample ingestion and legacy equivalence."""

from helpers import make_detection, make_measurement


def sample(source_id, sample_id, timestamp, detections, frame_index=0):
    return {
        "schema_version": 2,
        "source_id": source_id,
        "sample_id": sample_id,
        "timestamp": timestamp,
        "frame_index": frame_index,
        "entity_type": "person",
        "detections": detections,
    }


def detection(entity_id, x=100):
    return {
        "entity_id": entity_id,
        "label": "person",
        "confidence": .9,
        "bbox_px": [x, 100, x + 40, 260],
        "point_px": [x + 20, 260],
    }


def latest(client, source_id):
    body = client.get(
        "/api/v1/observations/latest-frames",
        params={"entity_type": "person", "source_id": source_id},
    ).json()
    return body["frames"][0]


def test_detection_sample_with_two_detections_is_completed(client, source_id):
    response = client.post(
        "/api/v1/detection-samples",
        json=sample(source_id, "frame-2", 1000.0, [detection("A"), detection("B", 300)], 20),
    )
    assert response.status_code == 200, response.text
    assert response.json()["sample_status"] == "completed"
    frame = latest(client, source_id)
    assert frame["source_frame_index"] == 20
    assert [row["entity_id"] for row in frame["detections"]] == ["A", "B"]


def test_detection_sample_with_one_detection(client, source_id):
    response = client.post(
        "/api/v1/detection-samples",
        json=sample(source_id, "frame-1", 1000.0, [detection("only")]),
    )
    assert response.status_code == 200
    assert latest(client, source_id)["observed_count"] == 1


def test_empty_detection_sample_is_explicit_known_zero(client, source_id):
    response = client.post(
        "/api/v1/detection-samples",
        json=sample(source_id, "frame-empty", 1000.0, [], 21),
    )
    assert response.status_code == 200, response.text
    assert response.json()["detection_count"] == 0
    frame = latest(client, source_id)
    assert frame["expected_count"] == 0
    assert frame["detections"] == []


def test_duplicate_detection_sample_retry_is_idempotent(client, source_id):
    payload = sample(source_id, "retry", 1000.0, [detection("A")])
    first = client.post("/api/v1/detection-samples", json=payload)
    second = client.post("/api/v1/detection-samples", json=payload)
    assert first.json()["sample_status"] == "completed"
    assert second.json()["sample_status"] == "duplicate"
    assert client.get("/api/v1/observations").json()["total"] == 2
    changed = {**payload, "detections": [detection("different")]}
    assert client.post("/api/v1/detection-samples", json=changed).status_code == 409


def test_invalid_detection_sample_is_atomic(client, source_id):
    payload = sample(source_id, "invalid", 1000.0, [
        detection("valid"),
        {**detection("broken"), "bbox_px": [1, 2, 3]},
    ])
    response = client.post("/api/v1/detection-samples", json=payload)
    assert response.status_code == 422
    assert client.get("/api/v1/observations").json()["total"] == 0

    non_finite = sample(source_id, "non-finite", 1000.0, [
        {**detection("broken"), "point_px": ["NaN", 260]},
    ])
    response = client.post("/api/v1/detection-samples", json=non_finite)
    assert response.status_code == 422
    assert client.get("/api/v1/observations").json()["total"] == 0


def test_out_of_order_sample_does_not_replace_newer_scene(client, source_id):
    client.post("/api/v1/detection-samples", json=sample(
        source_id, "newer", 1001.0, [detection("new")], 31))
    client.post("/api/v1/detection-samples", json=sample(
        source_id, "older", 1000.0, [detection("old")], 30))
    assert [row["entity_id"] for row in latest(client, source_id)["detections"]] == ["new"]


def test_same_timestamp_samples_from_multiple_sources_remain_independent(client, source_id):
    other = client.post("/api/v1/sources", json={"name": "Other", "kind": "webcam"}).json()["id"]
    for sid, entity in ((source_id, "A"), (other, "B")):
        response = client.post("/api/v1/detection-samples", json=sample(
            sid, f"source-{sid}", 1000.0, [detection(entity)]))
        assert response.status_code == 200
    frames = client.get("/api/v1/observations/latest-frames?entity_type=person").json()["frames"]
    assert {frame["source_id"] for frame in frames} == {source_id, other}


def test_new_and_legacy_detection_samples_materialize_equivalently(client, source_id):
    client.post("/api/v1/detection-samples", json=sample(
        source_id, "preferred", 1000.0, [detection("A")]))
    preferred = latest(client, source_id)
    legacy = {
        "observations": [
            make_detection(source_id, "legacy-d", 1001.0, entity_id="B",
                           bbox_px=(100, 100, 140, 260), sample_id="legacy"),
            make_measurement(source_id, "legacy-marker", 1001.0,
                             "detection_frame_count", 1, label="person",
                             sample_id="legacy"),
        ],
    }
    response = client.post("/api/v1/observations/batch", json=legacy)
    assert response.status_code == 200
    current = latest(client, source_id)
    assert preferred["expected_count"] == current["expected_count"] == 1
    assert current["sample_id"] == "legacy"
