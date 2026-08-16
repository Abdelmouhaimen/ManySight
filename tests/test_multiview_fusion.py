"""Geometry-first multi-camera association, current occupancy, history, and quality."""

from helpers import sync_live_state

from server import db
from server.services import alert_engine
from server.services.multiview import minimum_cost_assignment, refresh_freshness


def calibrate(client, source_id):
    response = client.put(f"/api/v1/sources/{source_id}/calibration", json={
        "points": [
            {"px": {"x": 0, "y": 0}, "map": {"x": 0, "y": 0}},
            {"px": {"x": 1000, "y": 0}, "map": {"x": 10, "y": 0}},
            {"px": {"x": 1000, "y": 1000}, "map": {"x": 10, "y": 10}},
            {"px": {"x": 0, "y": 1000}, "map": {"x": 0, "y": 10}},
        ], "frame_w": 1000, "frame_h": 1000,
    })
    assert response.status_code == 200, response.text


def post_sample(client, source_id, sample_id, ts, tracks, worker_id=None):
    observations = []
    for entity_id, x, y in tracks:
        observations.append({
            "schema_version": 2, "observation_id": f"{sample_id}-{entity_id}",
            "sample_id": sample_id, "kind": "detection", "timestamp": ts,
            "source_id": source_id, "entity_type": "person", "entity_id": entity_id,
            "confidence": 0.9, "geometry": {"point_px": [x * 100, y * 100]},
            "worker_id": worker_id,
        })
    observations.append({
        "schema_version": 2, "observation_id": f"{sample_id}-marker",
        "sample_id": sample_id, "kind": "measurement", "timestamp": ts,
        "source_id": source_id, "name": "detection_frame_count", "label": "person",
        "value": len(tracks), "unit": "tracks",
        "worker_id": worker_id,
    })
    response = client.post("/api/v1/observations/batch", json={"observations": observations})
    assert response.status_code == 200, response.text
    assert not response.json()["rejected"]
    # Several assertions below read the fused tables directly rather than
    # through an API that would drain the live scheduler for them.
    sync_live_state()


def setup_scene(client, calibrated_source):
    second = client.post("/api/v1/sources", json={"name": "Camera 2", "kind": "http"}).json()["id"]
    calibrate(client, second)
    zone = client.post("/api/v1/zones", json={
        "name": "Aisle 04", "ztype": "aisle",
        "polygon": [{"x": 0, "y": 0}, {"x": 10, "y": 0},
                    {"x": 10, "y": 10}, {"x": 0, "y": 10}],
    }).json()
    group = client.post("/api/v1/multiview/groups", json={
        "name": "Warehouse pair", "source_ids": [calibrated_source, second],
        "time_tolerance_s": 0.5, "spatial_gate_m": 0.8, "track_age_s": 30,
    }).json()
    return second, zone, group


def test_overlap_deduplicates_and_non_overlap_remains_distinct(client, calibrated_source):
    second, zone, group = setup_scene(client, calibrated_source)
    ts = db.now()
    post_sample(client, calibrated_source, "c1-f1", ts,
                [("cam1-a", 2.0, 2.0), ("cam1-b", 7.0, 7.0)])
    post_sample(client, second, "c2-f1", ts,
                [("cam2-x", 2.1, 2.0), ("cam2-y", 7.1, 7.0)])

    current = client.get("/api/v1/multiview/current", params={"group_id": group["id"]}).json()
    assert len(current["entities"]) == 2
    assert all(len(entity["members"]) == 2 for entity in current["entities"])
    occupancy = client.get("/api/v1/multiview/occupancy", params={
        "group_id": group["id"], "zone_id": zone["id"],
    }).json()
    assert occupancy["value"] == 2
    assert occupancy["quality"] == "known"

    # A distant source-local track must not be forced into either active fused identity.
    post_sample(client, second, "c2-f2", ts + 0.1, [("cam2-z", 9.5, 1.0)])
    current = client.get("/api/v1/multiview/current", params={"group_id": group["id"]}).json()
    assert len(current["entities"]) == 3


def test_global_assignment_avoids_greedy_multi_candidate_error():
    result = minimum_cost_assignment([[1.0, 2.0], [1.1, 100.0]])
    assert {(row, column) for row, column, _cost in result} == {(0, 1), (1, 0)}


def test_temporal_gate_same_source_constraint_and_worker_restart(client, calibrated_source):
    second, _zone, group = setup_scene(client, calibrated_source)
    ts = db.now()
    post_sample(client, calibrated_source, "two-local", ts,
                [("near-a", 2.0, 2.0), ("near-b", 2.1, 2.0)], worker_id=101)
    current = client.get("/api/v1/multiview/current", params={"group_id": group["id"]}).json()
    assert len(current["entities"]) == 2

    # Outside time tolerance: nearby geometry must not force a cross-camera match.
    post_sample(client, second, "late", ts + 1.0, [("other", 2.0, 2.0)], worker_id=201)
    current = client.get("/api/v1/multiview/current", params={"group_id": group["id"]}).json()
    assert len(current["entities"]) == 3

    # A reused source-local ID from another worker run receives a new fused identity.
    post_sample(client, calibrated_source, "new-run", ts + 1.1,
                [("near-a", 2.0, 2.0)], worker_id=102)
    memberships = db.q(
        "SELECT DISTINCT fused_entity_id,worker_id FROM fused_entity_members "
        "WHERE source_id=? AND local_entity_id='near-a'", (calibrated_source,))
    assert {row["worker_id"] for row in memberships} == {101, 102}
    assert len({row["fused_entity_id"] for row in memberships}) == 2


def test_crossing_trajectories_keep_source_local_memberships_stable(client, calibrated_source):
    second, _zone, group = setup_scene(client, calibrated_source)
    ts = db.now()
    post_sample(client, calibrated_source, "a0", ts, [("a", 2, 5), ("b", 8, 5)])
    post_sample(client, second, "b0", ts, [("x", 2.1, 5), ("y", 7.9, 5)])
    initial = {row["local_entity_id"]: row["fused_entity_id"] for row in db.q(
        "SELECT local_entity_id,fused_entity_id FROM fused_entity_members WHERE source_id=?",
        (calibrated_source,))}

    post_sample(client, calibrated_source, "a1", ts + 0.2, [("a", 6, 5), ("b", 4, 5)])
    post_sample(client, second, "b1", ts + 0.2, [("x", 6.1, 5), ("y", 3.9, 5)])
    after = {row["local_entity_id"]: row["fused_entity_id"] for row in db.q(
        "SELECT local_entity_id,fused_entity_id FROM fused_entity_members WHERE source_id=?",
        (calibrated_source,))}
    assert after["a"] == initial["a"]
    assert after["b"] == initial["b"]
    current = client.get("/api/v1/multiview/current", params={"group_id": group["id"]}).json()
    assert len(current["entities"]) == 2


def test_zero_samples_and_staleness_change_quality_without_fabricated_entities(client, calibrated_source):
    second, zone, group = setup_scene(client, calibrated_source)
    ts = db.now()
    post_sample(client, calibrated_source, "one-a", ts, [("a", 4, 4)])
    post_sample(client, second, "one-b", ts, [("b", 4.1, 4)])
    post_sample(client, calibrated_source, "zero-a", ts + 0.2, [])
    partial = client.get("/api/v1/multiview/occupancy", params={
        "group_id": group["id"], "zone_id": zone["id"],
    }).json()
    assert partial["value"] == 1
    assert partial["quality"] == "known"  # both sources still have complete fresh samples
    post_sample(client, second, "zero-b", ts + 0.2, [])
    cleared = client.get("/api/v1/multiview/occupancy", params={
        "group_id": group["id"], "zone_id": zone["id"],
    }).json()
    assert cleared["value"] == 0
    assert cleared["quality"] == "known"

    db.ex("UPDATE source_current_samples SET ts=ts-60")
    refresh_freshness()
    stale = client.get("/api/v1/multiview/occupancy", params={
        "group_id": group["id"], "zone_id": zone["id"],
    }).json()
    assert stale["quality"] == "unknown"


def test_saved_query_dashboard_and_edge_triggered_fused_alert(client, calibrated_source):
    second, zone, group = setup_scene(client, calibrated_source)
    ts = db.now()
    post_sample(client, calibrated_source, "alert-a", ts, [("a", 2, 2), ("b", 7, 7)])
    post_sample(client, second, "alert-b", ts, [("x", 2.1, 2), ("y", 7.1, 7)])

    query = client.post("/api/v1/queries", json={
        "name": "People in Aisle 04", "subject": "fused_entity",
        "measures": ["current_occupancy"],
        "filters": {"group_ids": [group["id"]], "zone_ids": [zone["id"]],
                    "entity_types": ["person"]},
    }).json()
    dashboard = client.post("/api/v1/dashboards", json={
        "name": "Warehouse live", "description": "Fused zone state", "created_by": "agent",
    }).json()
    widget = client.post(f"/api/v1/dashboards/{dashboard['id']}/widgets", json={
        "query_id": query["id"], "title": "Aisle 04 occupancy", "presentation": "number",
    })
    assert widget.status_code == 201, widget.text
    invalid = client.post(f"/api/v1/dashboards/{dashboard['id']}/widgets", json={
        "query_id": query["id"], "title": "Wrong shape", "presentation": "timeseries",
    })
    assert invalid.status_code == 422
    changed = client.patch(f"/api/v1/dashboard-widgets/{widget.json()['id']}", json={
        "presentation": "table",
    })
    assert changed.status_code == 200
    assert changed.json()["query_id"] == query["id"]

    rule = client.post("/api/v1/alert-rules", json={
        "name": "At least two people in Aisle 04", "kind": "query_condition",
        "params": {"query_id": query["id"]},
        "condition": {"operator": ">=", "value": 2}, "cooldown_s": 0,
    })
    assert rule.status_code == 201, rule.text
    fired = alert_engine.evaluate_ongoing(ts + 0.3, {zone["id"]: zone["name"]})
    assert len(fired) == 1
    assert fired[0]["payload"]["quality"] == "known"
    assert alert_engine.evaluate_ongoing(ts + 0.4, {zone["id"]: zone["name"]}) == []

    deleted = client.delete(f"/api/v1/dashboards/{dashboard['id']}").json()
    assert deleted["queries_preserved"] is True
    assert client.get(f"/api/v1/queries/{query['id']}").status_code == 200
