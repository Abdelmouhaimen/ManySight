"""Atomic detection-sample behavior across ordering, retries, and partial delivery."""

from helpers import make_detection, make_measurement
from server.services import current_state
from server import db


def detection(source_id, oid, ts, sid, entity="A"):
    return make_detection(source_id, oid, ts, entity_id=entity, point_px=(100, 200), sample_id=sid)


def marker(source_id, oid, ts, sid, count):
    return make_measurement(source_id, oid, ts, "detection_frame_count", count,
                            label="person", unit="tracks", sample_id=sid)


def current(client, source_id):
    response = client.get("/api/v1/observations/latest-frames",
                          params={"source_id": source_id, "entity_type": "person"})
    assert response.status_code == 200, response.text
    return response.json()["frames"]


def test_marker_first_commits_only_after_matching_detection(client, calibrated_source):
    first = client.post("/api/v1/observations/batch", json={"observations": [
        marker(calibrated_source, "m1", 1000.0, "sample-1", 1),
    ]})
    assert first.json()["completed_samples"] == 0
    assert current(client, calibrated_source) == []

    second = client.post("/api/v1/observations/batch", json={"observations": [
        detection(calibrated_source, "d1", 1000.0, "sample-1"),
    ]})
    assert second.json()["completed_samples"] == 1
    frame = current(client, calibrated_source)[0]
    assert frame["sample_id"] == "sample-1"
    assert [row["entity_id"] for row in frame["detections"]] == ["A"]


def test_partial_newer_sample_does_not_replace_complete_scene(client, calibrated_source):
    complete = [detection(calibrated_source, "d-old", 1000, "old"),
                marker(calibrated_source, "m-old", 1000, "old", 1)]
    client.post("/api/v1/observations/batch", json={"observations": complete})
    client.post("/api/v1/observations/batch", json={"observations": [
        detection(calibrated_source, "d-new", 1001, "new", "B"),
    ]})
    assert current(client, calibrated_source)[0]["sample_id"] == "old"


def test_completed_older_sample_cannot_replace_newer_scene(client, calibrated_source):
    client.post("/api/v1/observations/batch", json={"observations": [
        detection(calibrated_source, "new-d", 1001, "new", "newest"),
        marker(calibrated_source, "new-m", 1001, "new", 1),
    ]})
    client.post("/api/v1/observations/batch", json={"observations": [
        detection(calibrated_source, "old-d", 1000, "old", "older"),
        marker(calibrated_source, "old-m", 1000, "old", 1),
    ]})
    frame = current(client, calibrated_source)[0]
    assert frame["sample_id"] == "new"
    assert [row["entity_id"] for row in frame["detections"]] == ["newest"]


def test_sample_id_timestamp_mismatch_and_second_marker_are_rejected(client, calibrated_source):
    mismatch = client.post("/api/v1/observations/batch", json={"observations": [
        detection(calibrated_source, "d-bad", 1000, "bad"),
        marker(calibrated_source, "m-bad", 1001, "bad", 1),
    ]}).json()
    assert mismatch["accepted"] == 0
    assert {row["error"] for row in mismatch["rejected"]} == {"sample_timestamp_mismatch"}

    client.post("/api/v1/observations/batch", json={"observations": [
        marker(calibrated_source, "m-once", 1002, "once", 0),
    ]})
    duplicate = client.post("/api/v1/observations/batch", json={"observations": [
        marker(calibrated_source, "m-twice", 1002, "once", 0),
    ]}).json()
    assert duplicate["accepted"] == 0
    assert duplicate["rejected"][0]["error"] == "duplicate_completion_marker"


def test_legacy_timestamp_only_sample_remains_supported(client, calibrated_source):
    response = client.post("/api/v1/observations/batch", json={"observations": [
        detection(calibrated_source, "legacy-d", 1000, None),
        marker(calibrated_source, "legacy-m", 1000, None, 1),
    ]})
    assert response.status_code == 200
    frame = current(client, calibrated_source)[0]
    assert frame["sample_id"] is None
    assert frame["expected_count"] == 1


def test_current_scene_rebuilds_from_persisted_observations(client, calibrated_source):
    client.post("/api/v1/observations/batch", json={"observations": [
        detection(calibrated_source, "restart-d", 1000, "restart", "survivor"),
        marker(calibrated_source, "restart-m", 1000, "restart", 1),
    ]})
    db.ex("DELETE FROM source_current_entities")
    db.ex("DELETE FROM source_current_samples")
    current_state.rebuild_from_history()
    frame = current(client, calibrated_source)[0]
    assert frame["sample_id"] == "restart"
    assert [row["entity_id"] for row in frame["detections"]] == ["survivor"]
