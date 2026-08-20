"""Canonical multi-part zones and explicit camera-view extension provenance."""

import pytest

from server import db


def test_disjoint_multipolygon_round_trip_and_membership(client, calibrated_source):
    geometry = {"type": "MultiPolygon", "coordinates": [
        [[[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]]],
        [[[5, 5], [7, 5], [7, 7], [5, 7], [5, 5]]],
    ]}
    zone = client.post("/api/v1/zones", json={
        "name": "Two islands", "ztype": "area", "geometry": geometry,
    }).json()
    assert zone["geometry"]["type"] == "MultiPolygon"
    assert zone["component_count"] == 2

    batch = {"observations": [{
        "schema_version": 2, "observation_id": "island-two", "kind": "detection",
        "timestamp": 1000, "source_id": calibrated_source, "entity_type": "person",
        "entity_id": "P", "geometry": {"point_px": [600, 600]},
    }]}
    client.post("/api/v1/observations/batch", json=batch)
    rows = client.get("/api/v1/observations").json()["observations"]
    assert rows[0]["zone_id"] == zone["id"]


def test_zone_view_never_mutates_zone_without_explicit_extension(client, calibrated_source):
    zone = client.post("/api/v1/zones", json={
        "name": "Canonical", "polygon": [{"x": 0, "y": 0}, {"x": 1, "y": 0},
                                             {"x": 1, "y": 1}, {"x": 0, "y": 1}],
    }).json()
    view = client.post("/api/v1/zone-views", json={
        "zone_id": zone["id"], "source_id": calibrated_source,
        "outer_polygon_px": [{"x": 500, "y": 500}, {"x": 600, "y": 500},
                               {"x": 600, "y": 600}, {"x": 500, "y": 600}],
    }).json()
    unchanged = client.get(f"/api/v1/zones/{zone['id']}").json()
    assert unchanged["revision"] == 1

    extended = client.post(f"/api/v1/zone-views/{view['id']}/extend-zone",
                           json={"polygon": "outer"}).json()["zone"]
    assert extended["revision"] == 2
    assert extended["component_count"] == 2
    assert extended["geometry_provenance"][0]["operation"] == "extend_from_zone_view"


def test_overlapping_extension_unions_and_calibration_change_marks_provenance_stale(
        client, calibrated_source):
    zone = client.post("/api/v1/zones", json={
        "name": "Overlapping", "polygon": [
            {"x": 0, "y": 0}, {"x": 5, "y": 0}, {"x": 5, "y": 5}, {"x": 0, "y": 5}],
    }).json()
    view = client.post("/api/v1/zone-views", json={
        "zone_id": zone["id"], "source_id": calibrated_source,
        "outer_polygon_px": [
            {"x": 400, "y": 400}, {"x": 600, "y": 400},
            {"x": 600, "y": 600}, {"x": 400, "y": 600}],
    }).json()
    extended = client.post(f"/api/v1/zone-views/{view['id']}/extend-zone",
                           json={"polygon": "outer"}).json()["zone"]
    assert extended["component_count"] == 1
    assert extended["revision"] == 2
    assert extended["geometry_provenance"][0]["stale"] is False

    db.ex("UPDATE sources SET calibration_revision=calibration_revision+1 WHERE id=?",
          (calibrated_source,))
    refreshed = client.get(f"/api/v1/zones/{zone['id']}").json()
    assert refreshed["geometry_provenance"][0]["stale"] is True


def test_calibration_save_reprojects_camera_derived_zone(client, calibrated_source):
    zone = client.post("/api/v1/zones", json={
        "name": "Camera authored", "source_id": calibrated_source,
        "polygon_px": [
            {"x": 100, "y": 100}, {"x": 200, "y": 100},
            {"x": 200, "y": 200}, {"x": 100, "y": 200}],
    }).json()
    assert zone["polygon"][0] == {"x": 1.0, "y": 1.0}

    shifted_points = [
        {"px": {"x": 0, "y": 0}, "map": {"x": 10, "y": 0}},
        {"px": {"x": 1000, "y": 0}, "map": {"x": 20, "y": 0}},
        {"px": {"x": 1000, "y": 800}, "map": {"x": 20, "y": 8}},
        {"px": {"x": 0, "y": 800}, "map": {"x": 10, "y": 8}},
    ]
    response = client.put(f"/api/v1/sources/{calibrated_source}/calibration", json={
        "points": shifted_points, "frame_w": 1000, "frame_h": 800,
    })
    assert response.status_code == 200, response.text
    assert response.json()["refreshed_zones"] == [{"zone_id": zone["id"], "revision": 2}]

    refreshed = client.get(f"/api/v1/zones/{zone['id']}").json()
    assert refreshed["revision"] == 2
    assert min(point["x"] for point in refreshed["polygon"]) == pytest.approx(11)
    assert max(point["x"] for point in refreshed["polygon"]) == pytest.approx(12)
    assert refreshed["geometry_provenance"][-1]["operation"] == "refresh_after_calibration"
    assert refreshed["geometry_provenance"][-1]["stale"] is False


def test_calibration_save_does_not_overwrite_map_authored_zone(client, calibrated_source):
    zone = client.post("/api/v1/zones", json={
        "name": "Map authored", "polygon": [
            {"x": 0, "y": 0}, {"x": 2, "y": 0},
            {"x": 2, "y": 2}, {"x": 0, "y": 2}],
    }).json()
    points = [
        {"px": {"x": 0, "y": 0}, "map": {"x": 5, "y": 5}},
        {"px": {"x": 1000, "y": 0}, "map": {"x": 15, "y": 5}},
        {"px": {"x": 1000, "y": 800}, "map": {"x": 15, "y": 13}},
        {"px": {"x": 0, "y": 800}, "map": {"x": 5, "y": 13}},
    ]
    response = client.put(f"/api/v1/sources/{calibrated_source}/calibration", json={
        "points": points, "frame_w": 1000, "frame_h": 800,
    })
    assert response.status_code == 200, response.text
    assert response.json()["refreshed_zones"] == []
    assert client.get(f"/api/v1/zones/{zone['id']}").json()["geometry"] == zone["geometry"]
