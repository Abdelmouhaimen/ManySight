"""Canonical multi-part zones and explicit camera-view extension provenance."""

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
