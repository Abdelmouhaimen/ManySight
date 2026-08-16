"""Configuration caching must never be observable as staleness.

The pipeline caches zones, calibrations, projection surfaces, zone views,
multiview groups and the space revision so a 240 frames/second workload does not
re-read them per frame. Correctness is based on configuration writes, not on a
TTL, so every route that can change one of those has to be proved here: change
the configuration, submit the next sample, and require the new semantics.
"""
import pytest

from helpers import make_detection, sync_live_state

from server import db
from server.services import config_cache


def calibrate(client, source_id, scale=100.0):
    """1:1 mapping at `scale` pixels per metre."""
    response = client.put(f"/api/v1/sources/{source_id}/calibration", json={
        "points": [
            {"px": {"x": 0, "y": 0}, "map": {"x": 0, "y": 0}},
            {"px": {"x": 1000, "y": 0}, "map": {"x": 1000 / scale, "y": 0}},
            {"px": {"x": 1000, "y": 800}, "map": {"x": 1000 / scale, "y": 800 / scale}},
            {"px": {"x": 0, "y": 800}, "map": {"x": 0, "y": 800 / scale}},
        ], "frame_w": 1000, "frame_h": 800,
    })
    assert response.status_code == 200, response.text
    return response


def submit(client, source_id, observation_id, point_px=(500, 400), ts=1000.0):
    response = client.post("/api/v1/observations/batch", json={"observations": [
        make_detection(source_id, observation_id, ts, point_px=point_px)]})
    assert response.status_code == 200, response.text
    return response


def stored(client, observation_id):
    rows = client.get("/api/v1/observations").json()["observations"]
    return next(row for row in rows if row["observation_id"] == observation_id)


# --------------------------------------------------------------------------
# Statement classification
# --------------------------------------------------------------------------

@pytest.mark.parametrize("sql", [
    "INSERT INTO zones (name) VALUES (?)",
    "UPDATE zones SET polygon_json=? WHERE id=?",
    "DELETE FROM zones WHERE id=?",
    "UPDATE sources SET calibration_json=?,calibration_revision=? WHERE id=?",
    "DELETE FROM zone_views WHERE source_id=?",
    "INSERT INTO projection_surfaces (source_id,name) VALUES (?,?)",
    "UPDATE multiview_groups SET enabled=? WHERE id=?",
    "INSERT INTO alert_rules (name,kind) VALUES (?,?)",
    "UPDATE alert_rules SET enabled=0 WHERE id=?",
    "UPDATE stores SET current_space_revision_id=? WHERE id=1",
])
def test_configuration_writes_are_classified_as_invalidating(sql):
    assert config_cache.touches_configuration(sql) is True


@pytest.mark.parametrize("sql", [
    # Written once per ingested batch — must not invalidate geometry.
    "UPDATE sources SET event_count=event_count+?, last_ingestion_at=?, "
    "last_observation_at=CASE WHEN last_observation_at IS NULL OR last_observation_at<? "
    "THEN ? ELSE last_observation_at END WHERE id=?",
    # Written every time an alert fires.
    "UPDATE alert_rules SET last_fired_at=? WHERE id=?",
    "UPDATE alert_rules SET condition_state_json=? WHERE id=?",
    "UPDATE alert_rules SET condition_state_json='{}' WHERE id=?",
    # Nothing to do with configuration at all.
    "INSERT INTO events (source_id,ts) VALUES (?,?)",
    "INSERT INTO source_current_entities (source_id) VALUES (?)",
    "INSERT INTO fused_current_entities (fused_entity_id) VALUES (?) "
    "ON CONFLICT(fused_entity_id) DO UPDATE SET ts=excluded.ts",
    "INSERT INTO zone_current_occupancy (group_id) VALUES (?) "
    "ON CONFLICT(group_id) DO UPDATE SET value=excluded.value",
    "UPDATE jobs SET event_count=event_count+?, last_event_at=? WHERE id=?",
])
def test_runtime_writes_do_not_invalidate_configuration(sql):
    assert config_cache.touches_configuration(sql) is False


def test_an_unparseable_update_is_treated_as_invalidating():
    """The classifier's fallback is the conservative answer, not the fast one."""
    assert config_cache.touches_configuration(
        "UPDATE zones SET polygon_json=(SELECT x, y FROM other) WHERE id=?") is True


# --------------------------------------------------------------------------
# Every mutation route, through the real API
# --------------------------------------------------------------------------

def test_new_calibration_applies_to_the_next_sample(client, source_id):
    calibrate(client, source_id, scale=100.0)
    submit(client, source_id, "before")
    assert stored(client, "before")["geometry"]["point_map"]["x"] == pytest.approx(5.0)

    calibrate(client, source_id, scale=50.0)
    submit(client, source_id, "after")
    assert stored(client, "after")["geometry"]["point_map"]["x"] == pytest.approx(10.0)


def test_cleared_calibration_applies_to_the_next_sample(client, calibrated_source):
    submit(client, calibrated_source, "projected")
    assert stored(client, "projected")["geometry"]["point_map"] is not None

    assert client.delete(f"/api/v1/sources/{calibrated_source}/calibration").status_code == 200
    submit(client, calibrated_source, "unprojected")
    assert stored(client, "unprojected")["geometry"]["point_map"] is None


def test_a_new_zone_is_matched_by_the_next_sample(client, calibrated_source):
    submit(client, calibrated_source, "no-zone")
    assert stored(client, "no-zone")["zone_id"] is None

    zone = client.post("/api/v1/zones", json={
        "name": "New aisle", "ztype": "aisle",
        "polygon": [{"x": 0, "y": 0}, {"x": 9, "y": 0}, {"x": 9, "y": 7}, {"x": 0, "y": 7}],
    })
    assert zone.status_code == 201, zone.text
    submit(client, calibrated_source, "in-zone")
    assert stored(client, "in-zone")["zone_id"] == zone.json()["id"]


def test_a_moved_zone_is_matched_by_the_next_sample(client, calibrated_source):
    zone = client.post("/api/v1/zones", json={
        "name": "Movable", "ztype": "area",
        "polygon": [{"x": 0, "y": 0}, {"x": 9, "y": 0}, {"x": 9, "y": 7}, {"x": 0, "y": 7}],
    }).json()
    submit(client, calibrated_source, "inside")
    assert stored(client, "inside")["zone_id"] == zone["id"]

    moved = client.put(f"/api/v1/zones/{zone['id']}", json={
        "polygon": [{"x": 8, "y": 6}, {"x": 9, "y": 6}, {"x": 9, "y": 7}, {"x": 8, "y": 7}],
    })
    assert moved.status_code == 200, moved.text
    submit(client, calibrated_source, "outside")
    assert stored(client, "outside")["zone_id"] is None


def test_a_deleted_zone_is_no_longer_matched(client, calibrated_source):
    zone = client.post("/api/v1/zones", json={
        "name": "Doomed", "ztype": "area",
        "polygon": [{"x": 0, "y": 0}, {"x": 9, "y": 0}, {"x": 9, "y": 7}, {"x": 0, "y": 7}],
    }).json()
    submit(client, calibrated_source, "matched")
    assert stored(client, "matched")["zone_id"] == zone["id"]

    assert client.delete(f"/api/v1/zones/{zone['id']}").status_code == 200
    submit(client, calibrated_source, "unmatched")
    assert stored(client, "unmatched")["zone_id"] is None


def test_a_new_projection_surface_is_used_by_the_next_sample(client, calibrated_source):
    surface = client.post("/api/v1/projection-surfaces", json={
        "source_id": calibrated_source, "name": "Shelf", "kind": "shelf",
        "points": [
            {"px": {"x": 0, "y": 0}, "map": {"x": 100, "y": 100}},
            {"px": {"x": 1000, "y": 0}, "map": {"x": 110, "y": 100}},
            {"px": {"x": 1000, "y": 800}, "map": {"x": 110, "y": 108}},
            {"px": {"x": 0, "y": 800}, "map": {"x": 100, "y": 108}},
        ], "frame_w": 1000, "frame_h": 800,
    })
    assert surface.status_code == 201, surface.text
    response = client.post("/api/v1/observations/batch", json={"observations": [
        make_detection(calibrated_source, "on-surface", 1000.0, point_px=(500, 400),
                       projection_surface_id=surface.json()["id"])]})
    assert response.status_code == 200 and not response.json()["rejected"], response.text
    assert stored(client, "on-surface")["geometry"]["point_map"]["x"] == pytest.approx(105.0)


def test_a_new_zone_view_is_used_by_the_next_sample(client, calibrated_source):
    zone = client.post("/api/v1/zones", json={
        "name": "Viewed", "ztype": "area",
        "polygon": [{"x": 50, "y": 50}, {"x": 60, "y": 50}, {"x": 60, "y": 60}, {"x": 50, "y": 60}],
    }).json()
    submit(client, calibrated_source, "no-view")
    assert stored(client, "no-view")["zone_id"] is None

    view = client.post("/api/v1/zone-views", json={
        "zone_id": zone["id"], "source_id": calibrated_source,
        "outer_polygon_px": [{"x": 0, "y": 0}, {"x": 1000, "y": 0},
                             {"x": 1000, "y": 800}, {"x": 0, "y": 800}],
        "detection_polygon_px": [{"x": 0, "y": 0}, {"x": 1000, "y": 0},
                                 {"x": 1000, "y": 800}, {"x": 0, "y": 800}],
        "membership_rule": "point",
    })
    assert view.status_code == 201, view.text
    submit(client, calibrated_source, "with-view")
    assert stored(client, "with-view")["zone_id"] == zone["id"]


def test_disabling_a_multiview_group_stops_fusing_it(client, calibrated_source):
    second = client.post("/api/v1/sources", json={"name": "Second", "kind": "http"}).json()["id"]
    calibrate(client, second)
    group = client.post("/api/v1/multiview/groups", json={
        "name": "Pair", "source_ids": [calibrated_source, second], "track_age_s": 30,
    }).json()
    assert config_cache.groups_for_source(calibrated_source)

    patched = client.patch(f"/api/v1/multiview/groups/{group['id']}", json={"enabled": False})
    assert patched.status_code == 200, patched.text
    assert config_cache.groups_for_source(calibrated_source) == []

    sample = client.post("/api/v1/detection-samples", json={
        "schema_version": 2, "source_id": calibrated_source, "sample_id": "disabled-1",
        "timestamp": db.now(), "entity_type": "person",
        "detections": [{"entity_id": "a", "point_px": [200, 200]}]})
    assert sample.status_code == 200, sample.text
    sync_live_state()
    assert db.q("SELECT * FROM fused_entities") == []


def test_creating_the_first_alert_rule_enables_batch_evaluation(client, calibrated_source):
    assert config_cache.enabled_alert_rule_count() == 0
    rule = client.post("/api/v1/alert-rules", json={
        "name": "Any detection", "kind": "event_match",
        "params": {"event_type": "detection"}, "cooldown_s": 0,
    })
    assert rule.status_code == 201, rule.text
    assert config_cache.enabled_alert_rule_count() == 1

    submit(client, calibrated_source, "fires")
    assert client.get("/api/v1/alerts").json()


def test_firing_an_alert_does_not_invalidate_geometry(client, calibrated_source):
    """Cooldown bookkeeping is runtime state on a configuration table."""
    client.post("/api/v1/zones", json={
        "name": "Watched", "ztype": "area",
        "polygon": [{"x": 0, "y": 0}, {"x": 9, "y": 0}, {"x": 9, "y": 7}, {"x": 0, "y": 7}]})
    client.post("/api/v1/alert-rules", json={
        "name": "Any detection", "kind": "event_match",
        "params": {"event_type": "detection"}, "cooldown_s": 0})
    submit(client, calibrated_source, "warm")
    generation = config_cache.generation()

    # Far enough apart to clear the rule cooldown, so `last_fired_at` really is
    # written a second time.
    submit(client, calibrated_source, "fires-again", ts=1200.0)
    assert len(client.get("/api/v1/alerts").json()) >= 2
    assert config_cache.generation() == generation


def test_ingesting_observations_does_not_invalidate_configuration(client, calibrated_source):
    submit(client, calibrated_source, "warm")
    generation = config_cache.generation()
    for index in range(5):
        submit(client, calibrated_source, f"hot-{index}", ts=1000.0 + index)
    assert config_cache.generation() == generation


def test_space_reinitialization_invalidates_everything(client, calibrated_source):
    client.post("/api/v1/zones", json={
        "name": "Gone soon", "ztype": "area",
        "polygon": [{"x": 0, "y": 0}, {"x": 9, "y": 0}, {"x": 9, "y": 7}, {"x": 0, "y": 7}]})
    submit(client, calibrated_source, "before-reset")
    assert stored(client, "before-reset")["zone_id"] is not None

    reset = client.post("/api/v1/workspace/reinitialize-space",
                        json={"confirmation": "REINITIALIZE SPACE", "history": "keep"})
    assert reset.status_code == 200, reset.text
    assert config_cache.geometry_context()[0] == []
    assert db.current_space_revision_id() == reset.json()["space_revision_id"]


def test_prepared_zone_containment_matches_the_uncached_predicate():
    """Same boundary semantics, including a point exactly on the edge."""
    from server.services import zone_geometry
    geometry = {"type": "Polygon", "coordinates": [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]]}
    prepared = zone_geometry.prepare(geometry)
    for x, y in [(5, 5), (0, 0), (0, 5), (10, 10), (10, 5), (-0.0001, 5),
                 (10.0001, 5), (5, -1), (5, 11), (1e6, 1e6)]:
        assert prepared.covers(x, y) == zone_geometry.contains(geometry, x, y), (x, y)


def test_prepared_containment_matches_for_a_multipolygon_with_a_hole():
    from server.services import zone_geometry
    geometry = {"type": "Polygon", "coordinates": [
        [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]],
        [[4, 4], [6, 4], [6, 6], [4, 6], [4, 4]],
    ]}
    prepared = zone_geometry.prepare(geometry)
    for x, y in [(5, 5), (4, 4), (4, 5), (3.9, 5), (6, 6), (1, 1), (9.9, 9.9)]:
        assert prepared.covers(x, y) == zone_geometry.contains(geometry, x, y), (x, y)
