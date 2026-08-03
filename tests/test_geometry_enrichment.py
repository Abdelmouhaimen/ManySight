"""Geometry enrichment shared by /events and /observations (services/enrich.py)."""
import pytest

from helpers import make_detection


def test_point_projection_via_calibration(client, calibrated_source):
    # calibrated_source uses a 100px = 1m mapping; pixel (500, 400) -> map (5, 4)
    body = {"observations": [make_detection(calibrated_source, "geo-1", 1000.0, point_px=(500, 400))]}
    client.post("/api/v1/observations/batch", json=body)
    row = client.get("/api/v1/observations", params={"source_id": calibrated_source}).json()["observations"][0]
    assert row["geometry"]["point_map"]["x"] == pytest.approx(5.0, abs=0.05)
    assert row["geometry"]["point_map"]["y"] == pytest.approx(4.0, abs=0.05)
    assert row["projection_method"] == "floor"


def test_bbox_bottom_center_fallback(client, calibrated_source):
    body = {"observations": [make_detection(
        calibrated_source, "geo-2", 1000.0, point_px=None,
        geometry={"bbox_px": [400, 300, 600, 500]},  # x0,y0,x1,y1 -> bottom-center (500, 500)
    )]}
    client.post("/api/v1/observations/batch", json=body)
    row = client.get("/api/v1/observations", params={"source_id": calibrated_source}).json()["observations"][0]
    assert row["geometry"]["point_kind"] == "bbox_bottom_center"
    assert row["geometry"]["point_px"]["x"] == pytest.approx(500.0)
    assert row["geometry"]["point_px"]["y"] == pytest.approx(500.0)


def test_foot_keypoint_fallback_when_no_point_or_bbox(client, calibrated_source):
    body = {"observations": [{
        "schema_version": 2, "observation_id": "geo-3", "kind": "detection", "timestamp": 1000.0,
        "source_id": calibrated_source, "entity_id": "e1",
        "geometry": {"keypoints_px": {"left_ankle": [100, 200], "right_ankle": [120, 200]}},
    }]}
    client.post("/api/v1/observations/batch", json=body)
    row = client.get("/api/v1/observations", params={"source_id": calibrated_source}).json()["observations"][0]
    assert row["geometry"]["point_kind"] == "foot_keypoints"
    assert row["geometry"]["point_px"]["x"] == pytest.approx(110.0)


def test_mask_only_leaves_point_empty_and_is_still_stored(client, calibrated_source):
    body = {"observations": [{
        "schema_version": 2, "observation_id": "geo-4", "kind": "detection", "timestamp": 1000.0,
        "source_id": calibrated_source, "entity_id": "e1",
        "geometry": {"mask": {"rle": "deadbeef"}},
    }]}
    response = client.post("/api/v1/observations/batch", json=body)
    assert response.json()["accepted"] == 1
    row = client.get("/api/v1/observations", params={"source_id": calibrated_source}).json()["observations"][0]
    assert row["geometry"]["point_px"] is None
    assert row["zone_id"] is None
    assert row["geometry"]["mask"] == {"rle": "deadbeef"}


def test_missing_calibration_stores_observation_without_projection(client, source_id):
    body = {"observations": [make_detection(source_id, "geo-5", 1000.0, point_px=(500, 400))]}
    response = client.post("/api/v1/observations/batch", json=body)
    assert response.status_code == 200
    assert response.json()["accepted"] == 1
    row = client.get("/api/v1/observations", params={"source_id": source_id}).json()["observations"][0]
    assert row["geometry"]["point_map"] is None
    assert row["zone_id"] is None


def test_zone_auto_assignment_from_map_point(client, calibrated_source):
    zone = client.post("/api/v1/zones", json={
        "name": "Checkout", "ztype": "checkout",
        "polygon": [{"x": 4, "y": 3}, {"x": 6, "y": 3}, {"x": 6, "y": 5}, {"x": 4, "y": 5}],
    }).json()
    body = {"observations": [make_detection(calibrated_source, "geo-6", 1000.0, point_px=(500, 400))]}
    client.post("/api/v1/observations/batch", json=body)
    row = client.get("/api/v1/observations", params={"source_id": calibrated_source}).json()["observations"][0]
    assert row["zone_id"] == zone["id"]
    assert row["zone_assignment_method"] == "map_point"


def test_geometry_revision_retained_after_zone_edit(client, calibrated_source):
    zone = client.post("/api/v1/zones", json={
        "name": "Checkout", "ztype": "checkout",
        "polygon": [{"x": 4, "y": 3}, {"x": 6, "y": 3}, {"x": 6, "y": 5}, {"x": 4, "y": 5}],
    }).json()
    body = {"observations": [make_detection(calibrated_source, "geo-7", 1000.0, point_px=(500, 400))]}
    client.post("/api/v1/observations/batch", json=body)
    row_before = client.get("/api/v1/observations", params={"source_id": calibrated_source}).json()["observations"][0]
    assert row_before["revisions"]["zone"] == zone["revision"]
    client.put(f"/api/v1/zones/{zone['id']}", json={"name": "Checkout renamed"})
    row_after = client.get(f"/api/v1/observations/{row_before['id']}").json()
    # Historical row keeps the revision active when it was ingested, even
    # though the zone has since moved to a new revision.
    assert row_after["revisions"]["zone"] == row_before["revisions"]["zone"]


def test_worker_never_accepts_zone_view_from_wrong_source(client, calibrated_source, source_id):
    zone = client.post("/api/v1/zones", json={
        "name": "Aisle", "polygon": [{"x": 0, "y": 0}, {"x": 1, "y": 0}, {"x": 1, "y": 1}],
    }).json()
    view = client.post("/api/v1/zone-views", json={
        "zone_id": zone["id"], "source_id": calibrated_source,
        "outer_polygon_px": [{"x": 0, "y": 0}, {"x": 10, "y": 0}, {"x": 10, "y": 10}],
    }).json()
    body = {"observations": [make_detection(source_id, "geo-8", 1000.0, point_px=(5, 5),
                                            zone_view_id=view["id"])]}
    response = client.post("/api/v1/observations/batch", json=body)
    assert response.json()["rejected"][0]["error"] == "invalid_observation"
