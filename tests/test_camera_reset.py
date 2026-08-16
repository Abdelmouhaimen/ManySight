"""Reset cameras: remove the cameras, keep the building.

The existing resets deliberately preserve sources — reinitialize-space keeps the
same hardware to re-place, reinitialize-observations keeps everything but the
evidence. This is the operation for "these cameras are gone, let me start again",
so what it must not touch is as much of the contract as what it removes.
"""
import json

import pytest

from helpers import sync_live_state

from server import db
from server.services import camera_reset

CALIBRATION = {
    "points": [
        {"px": {"x": 0, "y": 0}, "map": {"x": 0, "y": 0}},
        {"px": {"x": 1000, "y": 0}, "map": {"x": 10, "y": 0}},
        {"px": {"x": 1000, "y": 800}, "map": {"x": 10, "y": 8}},
        {"px": {"x": 0, "y": 800}, "map": {"x": 0, "y": 8}},
    ],
    "frame_w": 1000, "frame_h": 800,
}
AISLE = [{"x": 2, "y": 1}, {"x": 6, "y": 1}, {"x": 6, "y": 7}, {"x": 2, "y": 7}]
FLOOR_PX = [{"x": 100, "y": 100}, {"x": 500, "y": 100}, {"x": 500, "y": 600}, {"x": 100, "y": 600}]


def preview(client):
    response = client.post("/api/v1/workspace/reset-cameras", json={})
    assert response.status_code == 200, response.text
    return response.json()


def execute(client, token=None, confirmation="RESET CAMERAS"):
    body = {"dry_run": False, "confirmation": confirmation}
    if token:
        body["reset_token"] = token
    return client.post("/api/v1/workspace/reset-cameras", json=body)


def make_camera(client, name, calibrated=True, placed=True):
    source_id = client.post("/api/v1/sources", json={
        "name": name, "kind": "http", "connection_management": "storelens_managed",
        "connection": {"url": f"http://{name.replace(' ', '-').lower()}.local/stream.mjpg"},
    }).json()["id"]
    if placed:
        client.put(f"/api/v1/sources/{source_id}/placement",
                   json={"x": 1.0, "y": 1.0, "rotation_deg": 0, "fov_deg": 70})
    if calibrated:
        assert client.put(f"/api/v1/sources/{source_id}/calibration",
                          json=CALIBRATION).status_code == 200
    return source_id


@pytest.fixture
def workspace(client):
    """A fully configured workspace: four cameras, a zone, a group, evidence, an alert."""
    client.put("/api/v1/store", json={
        "name": "Warehouse", "width_m": 12, "height_m": 10,
        "map": {"walls": [[{"x": 0, "y": 0}, {"x": 12, "y": 0}]]}})
    sources = [make_camera(client, f"Camera {index}") for index in range(1, 5)]
    zone = client.post("/api/v1/zones", json={
        "name": "Aisle 04", "ztype": "aisle", "polygon": AISLE}).json()
    keep_zone = client.post("/api/v1/zones", json={
        "name": "Entrance", "ztype": "entrance",
        "polygon": [{"x": 8, "y": 8}, {"x": 11, "y": 8},
                    {"x": 11, "y": 9}, {"x": 8, "y": 9}]}).json()
    for source_id in sources[:2]:
        assert client.post("/api/v1/zone-views", json={
            "zone_id": zone["id"], "source_id": source_id,
            "outer_polygon_px": FLOOR_PX}).status_code == 201
    assert client.post("/api/v1/projection-surfaces", json={
        "source_id": sources[0], "name": "Shelf", "kind": "shelf",
        "points": [
            {"px": {"x": 0, "y": 0}, "map": {"x": 0, "y": 0}},
            {"px": {"x": 1000, "y": 0}, "map": {"x": 10, "y": 0}},
            {"px": {"x": 1000, "y": 800}, "map": {"x": 10, "y": 8}},
            {"px": {"x": 0, "y": 800}, "map": {"x": 0, "y": 8}},
        ], "frame_w": 1000, "frame_h": 800}).status_code == 201
    group = client.post("/api/v1/multiview/groups", json={
        "name": "Warehouse floor", "source_ids": sources,
        "time_tolerance_s": 0.5, "spatial_gate_m": 1.0, "track_age_s": 30}).json()

    job = client.post("/api/v1/jobs", json={
        "name": "Person tracking", "source_ids": sources, "event_types": ["detection"]}).json()
    worker = client.post("/api/v1/workers", json={
        "job_id": job["id"], "name": "tracker", "version": "1"}).json()
    client.post(f"/api/v1/workers/{worker['id']}/heartbeat", json={"status": "running"})

    ts = db.now()
    for index, source_id in enumerate(sources):
        assert client.post("/api/v1/detection-samples", json={
            "schema_version": 2, "source_id": source_id, "sample_id": f"s{index}",
            "timestamp": ts, "entity_type": "person",
            "detections": [{"entity_id": f"t{index}", "point_px": [300, 200],
                            "confidence": 0.9}]}).status_code == 200
    sync_live_state()

    query = client.post("/api/v1/queries", json={
        "name": "People in Aisle 04", "subject": "fused_entity",
        "measures": ["current_occupancy"],
        "filters": {"group_ids": [group["id"]], "zone_ids": [zone["id"]],
                    "entity_types": ["person"]}, "created_by": "agent"}).json()
    unaffected_query = client.post("/api/v1/queries", json={
        "name": "Everything", "subject": "detection", "measures": ["observations"]}).json()
    rule = client.post("/api/v1/alert-rules", json={
        "name": "More than 2 people in Aisle 04", "kind": "query_condition",
        "params": {"query_id": query["id"]},
        "condition": {"operator": ">", "value": 2}}).json()
    other_rule = client.post("/api/v1/alert-rules", json={
        "name": "Any detection", "kind": "event_match",
        "params": {"event_type": "detection"}}).json()
    return {"sources": sources, "zone": zone, "keep_zone": keep_zone, "group": group,
            "query": query, "unaffected_query": unaffected_query, "rule": rule,
            "other_rule": other_rule, "job": job, "worker": worker}


# ---------------------------------------------------------------------------
# preview and confirmation
# ---------------------------------------------------------------------------

def _stable_state(client):
    """Everything a reset would change, minus the fields that track wall time."""
    volatile = {"observation_age_s", "last_ingestion_at", "latest_runtime"}
    return {
        "sources": [{key: value for key, value in source.items() if key not in volatile}
                    for source in client.get("/api/v1/sources").json()],
        "zones": client.get("/api/v1/zones").json(),
        "groups": client.get("/api/v1/multiview/groups").json(),
        "views": client.get("/api/v1/zone-views").json(),
        "surfaces": client.get("/api/v1/projection-surfaces").json(),
        "rules": client.get("/api/v1/alert-rules").json(),
        "queries": client.get("/api/v1/queries").json(),
        "events": client.get("/api/v1/observations").json()["total"],
        "current_samples": db.q("SELECT * FROM source_current_samples ORDER BY source_id"),
        "fused": db.q("SELECT * FROM fused_current_entities ORDER BY fused_entity_id"),
        "jobs": db.q("SELECT id, source_ids FROM jobs ORDER BY id"),
        "workers": db.q("SELECT id, desired_state FROM worker_instances ORDER BY id"),
    }


def test_dry_run_reports_the_impact_and_changes_nothing(client, workspace):
    before = _stable_state(client)
    body = preview(client)

    assert body["reset"] is False and body["dry_run"] is True
    assert body["confirmation_required"] == "RESET CAMERAS"
    impact = body["impact"]
    assert impact["cameras"] == 4
    assert impact["source_ids"] == workspace["sources"]
    assert impact["calibrations"] == 4
    assert impact["placements"] == 4
    assert impact["zone_views"] == 2
    assert impact["projection_surfaces"] == 1
    assert impact["multiview_groups"] == 1
    assert impact["observations"] > 0
    assert impact["current_samples"] == 4
    assert impact["preserved"]["canonical_zones"] == 2
    assert [rule["id"] for rule in impact["alert_rules_to_disable"]] == [workspace["rule"]["id"]]
    assert [item["id"] for item in impact["saved_queries_becoming_stale"]] \
        == [workspace["query"]["id"]]
    assert [worker["worker_id"] for worker in impact["workers_to_stop"]] \
        == [workspace["worker"]["id"]]

    assert _stable_state(client) == before, "a dry run must not change anything"


def test_execution_requires_the_exact_confirmation(client, workspace):
    for confirmation in ("", "reset cameras", "RESET CAMERA", "REINITIALIZE SPACE"):
        response = execute(client, confirmation=confirmation)
        assert response.status_code == 422, confirmation
    assert len(client.get("/api/v1/sources").json()) == 4


def test_a_preview_cannot_delete_a_camera_it_never_listed(client, workspace):
    """The stale-preview guard: a camera added after the preview refuses the reset."""
    token = preview(client)["impact"]["reset_token"]
    latecomer = make_camera(client, "Camera 5")

    refused = execute(client, token=token)
    assert refused.status_code == 409
    assert refused.json()["detail"]["reason"] == "stale_preview"
    assert len(client.get("/api/v1/sources").json()) == 5

    # Previewing again describes the new set, and that token works.
    fresh = preview(client)
    assert fresh["impact"]["cameras"] == 5
    assert latecomer in fresh["impact"]["source_ids"]
    assert execute(client, token=fresh["impact"]["reset_token"]).status_code == 200


# ---------------------------------------------------------------------------
# what is removed
# ---------------------------------------------------------------------------

def test_reset_removes_every_camera_and_its_dependent_state(client, workspace):
    token = preview(client)["impact"]["reset_token"]
    response = execute(client, token=token)
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["reset"] is True and body["already_empty"] is False
    assert body["removed"]["cameras"] == 4
    assert body["removed"]["multiview_groups"] == 1

    assert client.get("/api/v1/sources").json() == []
    assert client.get("/api/v1/multiview/groups").json() == []
    assert client.get("/api/v1/zone-views").json() == []
    assert client.get("/api/v1/projection-surfaces").json() == []
    assert db.q("SELECT * FROM source_credentials") == []
    assert db.q("SELECT * FROM camera_calibrations") == []
    assert db.q("SELECT * FROM source_current_samples") == []
    assert db.q("SELECT * FROM source_current_entities") == []
    assert db.q("SELECT * FROM events WHERE source_id IS NOT NULL") == []
    assert db.q("SELECT * FROM fused_entities") == []
    assert db.q("SELECT * FROM fused_entity_members") == []
    assert db.q("SELECT * FROM fused_observations") == []
    assert db.q("SELECT * FROM fused_current_entities") == []
    assert db.q("SELECT * FROM zone_current_occupancy") == []
    assert db.q("SELECT * FROM zone_occupancy_observations") == []
    assert db.q("SELECT * FROM zone_geometry_provenance WHERE source_id IS NOT NULL") == []


def test_reset_leaves_no_dangling_source_or_group_reference(client, workspace):
    execute(client).status_code == 200
    live_sources = {row["id"] for row in db.q("SELECT id FROM sources")}
    live_groups = {row["id"] for row in db.q("SELECT id FROM multiview_groups")}
    assert not live_sources and not live_groups

    for table, column in (("zone_views", "source_id"), ("projection_surfaces", "source_id"),
                          ("camera_calibrations", "source_id"), ("source_credentials", "source_id"),
                          ("source_current_samples", "source_id"),
                          ("source_current_entities", "source_id")):
        assert db.q(f"SELECT {column} FROM {table}") == [], table
    for table in ("fused_entities", "fused_observations", "fused_current_entities",
                  "zone_current_occupancy", "zone_occupancy_observations"):
        assert db.q(f"SELECT group_id FROM {table}") == [], table
    # A job keeps its identity but loses the bindings that no longer resolve.
    for job in db.q("SELECT source_ids FROM jobs"):
        assert db.jload(job["source_ids"], []) == []


def test_a_removed_source_id_can_no_longer_accept_observations(client, workspace):
    removed = workspace["sources"][0]
    execute(client)
    response = client.post("/api/v1/detection-samples", json={
        "schema_version": 2, "source_id": removed, "sample_id": "after-reset",
        "timestamp": db.now(), "entity_type": "person", "detections": []})
    assert response.status_code == 404


def test_a_running_worker_is_asked_to_stop_through_its_heartbeat(client, workspace):
    body = execute(client).json()
    assert [item["worker_id"] for item in body["workers_stop_requested"]] \
        == [workspace["worker"]["id"]]
    assert "cannot terminate a process it never started" in body["workers_note"]
    beat = client.post(f"/api/v1/workers/{workspace['worker']['id']}/heartbeat",
                       json={"status": "running"})
    assert beat.status_code == 200
    assert beat.json()["should_stop"] is True


# ---------------------------------------------------------------------------
# what is preserved
# ---------------------------------------------------------------------------

def test_reset_preserves_the_workspace_floor_plan_and_canonical_zones(client, workspace):
    store_before = client.get("/api/v1/store").json()
    execute(client)

    store_after = client.get("/api/v1/store").json()
    assert store_after["name"] == store_before["name"] == "Warehouse"
    assert store_after["width_m"] == 12 and store_after["height_m"] == 10
    assert store_after["map"] == store_before["map"]

    zones = client.get("/api/v1/zones").json()
    assert sorted(zone["name"] for zone in zones) == ["Aisle 04", "Entrance"]
    aisle = next(zone for zone in zones if zone["name"] == "Aisle 04")
    # The physical region survives; only its camera views are gone.
    assert aisle["geometry"]["type"] == "Polygon"
    assert not [view for view in client.get("/api/v1/zone-views").json()
                if view["zone_id"] == aisle["id"]]


def test_reset_keeps_saved_query_and_dashboard_definitions(client, workspace):
    dashboard = client.post("/api/v1/dashboards", json={"name": "Ops"}).json()
    client.post(f"/api/v1/dashboards/{dashboard['id']}/widgets", json={
        "query_id": workspace["query"]["id"], "title": "Aisle 04", "presentation": "number"})
    execute(client)

    queries = {item["id"] for item in client.get("/api/v1/queries").json()}
    assert {workspace["query"]["id"], workspace["unaffected_query"]["id"]} <= queries
    widgets = client.get(f"/api/v1/dashboards/{dashboard['id']}").json()["widgets"]
    assert [widget["query_id"] for widget in widgets] == [workspace["query"]["id"]]


# ---------------------------------------------------------------------------
# dependent analytics
# ---------------------------------------------------------------------------

def test_affected_alerts_are_disabled_and_unaffected_ones_are_not(client, workspace):
    body = execute(client).json()
    assert [item["id"] for item in body["alert_rules_disabled"]] == [workspace["rule"]["id"]]
    assert body["alert_rules_disabled"][0]["stale_references"][0]["kind"] == "saved query"

    rules = {rule["id"]: rule for rule in client.get("/api/v1/alert-rules").json()}
    assert rules[workspace["rule"]["id"]]["enabled"] is False
    # The definition is kept so the user can point it at new cameras.
    assert rules[workspace["rule"]["id"]]["condition"] == {"operator": ">", "value": 2}
    assert rules[workspace["other_rule"]["id"]]["enabled"] is True


def test_stale_saved_queries_are_reported_not_rewritten(client, workspace):
    body = execute(client).json()
    stale = body["saved_queries_now_stale"]
    assert [item["id"] for item in stale] == [workspace["query"]["id"]]
    assert stale[0]["stale_references"][0]["id"] == workspace["group"]["id"]

    stored = client.get(f"/api/v1/queries/{workspace['query']['id']}").json()
    assert stored["filters"]["group_ids"] == [workspace["group"]["id"]], \
        "the definition must not be silently rewritten to some other id"
    # Running it surfaces the broken reference instead of answering zero.
    failed = client.post(f"/api/v1/queries/{workspace['query']['id']}/execute")
    assert failed.status_code == 409
    assert failed.json()["detail"]["code"] == "unresolved_query_reference"


# ---------------------------------------------------------------------------
# edge cases
# ---------------------------------------------------------------------------

def test_reset_on_an_empty_workspace_succeeds_and_reports_zero(client):
    body = preview(client)
    assert body["impact"]["cameras"] == 0

    response = execute(client)
    assert response.status_code == 200
    assert response.json()["already_empty"] is True
    assert response.json()["removed"]["cameras"] == 0


def test_reset_of_a_single_camera(client):
    source_id = make_camera(client, "Only camera")
    assert preview(client)["impact"]["cameras"] == 1
    body = execute(client).json()
    assert body["removed"]["cameras"] == 1
    assert body["removed"]["source_ids"] == [source_id]
    assert client.get("/api/v1/sources").json() == []


def test_repeating_the_reset_is_idempotent(client, workspace):
    first = execute(client).json()
    assert first["removed"]["cameras"] == 4
    second = execute(client)
    assert second.status_code == 200
    assert second.json()["already_empty"] is True
    assert second.json()["removed"]["cameras"] == 0
    assert client.get("/api/v1/sources").json() == []


def test_new_cameras_can_be_configured_after_a_reset(client, workspace):
    execute(client)
    fresh = make_camera(client, "Replacement camera")
    assert client.put(f"/api/v1/sources/{fresh}/calibration",
                      json=CALIBRATION).status_code == 200
    zone = next(item for item in client.get("/api/v1/zones").json()
                if item["name"] == "Aisle 04")
    view = client.post("/api/v1/zone-views", json={
        "zone_id": zone["id"], "source_id": fresh, "outer_polygon_px": FLOOR_PX})
    assert view.status_code == 201, "a preserved zone must accept a view from a new camera"
    sample = client.post("/api/v1/detection-samples", json={
        "schema_version": 2, "source_id": fresh, "sample_id": "fresh-1",
        "timestamp": db.now(), "entity_type": "person", "detections": []})
    assert sample.status_code == 200


def test_the_other_resets_still_keep_their_cameras(client, workspace):
    """Reset cameras is the only one that removes sources; that is the point."""
    assert client.post("/api/v1/workspace/reinitialize-observations",
                       json={"confirmation": "REINITIALIZE OBSERVATIONS"}).status_code == 200
    assert len(client.get("/api/v1/sources").json()) == 4
    assert client.post("/api/v1/workspace/reinitialize-space",
                       json={"confirmation": "REINITIALIZE SPACE",
                             "history": "keep"}).status_code == 200
    assert len(client.get("/api/v1/sources").json()) == 4


# ---------------------------------------------------------------------------
# guided-demo isolation
# ---------------------------------------------------------------------------

def test_reset_is_refused_inside_a_guided_demo_session(client, workspace, tmp_path):
    """A demo session swaps the whole workspace; a reset there would destroy the fixture."""
    demo_db = tmp_path / "demo-session.db"
    db.init_db(str(demo_db))
    with db.using_database(str(demo_db)):
        assert camera_reset.in_isolated_demo_workspace() is True
        with pytest.raises(Exception) as raised:
            from server.routers import workspace as workspace_router
            workspace_router.reset_cameras(workspace_router.CameraResetIn())
        assert "exit the guided demo" in str(raised.value)
    assert camera_reset.in_isolated_demo_workspace() is False
    assert len(client.get("/api/v1/sources").json()) == 4


# ---------------------------------------------------------------------------
# SDK
# ---------------------------------------------------------------------------

def test_sdk_previews_then_resets(client, workspace, monkeypatch):
    import sys
    sys.path.insert(0, "sdk/python")
    from storelens import StoreLens

    sdk = StoreLens("http://testserver")
    monkeypatch.setattr(sdk, "_req", lambda method, path, body=None, params=None:
                        client.request(method, "/api/v1" + path, json=body).json())

    previewed = sdk.preview_reset_cameras()
    assert previewed["dry_run"] is True
    assert previewed["impact"]["cameras"] == 4
    assert len(client.get("/api/v1/sources").json()) == 4, "preview must not mutate"

    # Without confirm it is still only a preview.
    assert sdk.reset_cameras()["dry_run"] is True
    assert len(client.get("/api/v1/sources").json()) == 4

    result = sdk.reset_cameras(confirm=True, reset_token=previewed["impact"]["reset_token"])
    assert result["reset"] is True and result["removed"]["cameras"] == 4
    assert client.get("/api/v1/sources").json() == []


# ---------------------------------------------------------------------------
# MCP
# ---------------------------------------------------------------------------

def test_mcp_reset_tool_previews_by_default_and_confirms_explicitly(client, workspace, monkeypatch):
    import mcp_server.server as mcp_server

    calls = []

    def fake_req(method, path, body=None, raw=False, privileged=False):
        calls.append((method, path, body))
        return client.request(method, "/api/v1" + path, json=body).json()

    monkeypatch.setattr(mcp_server, "_req", fake_req)

    previewed = mcp_server.reset_cameras()
    assert previewed["dry_run"] is True
    assert calls[-1][2] == {"dry_run": True}
    assert len(client.get("/api/v1/sources").json()) == 4

    token = previewed["impact"]["reset_token"]
    done = mcp_server.reset_cameras(confirmed=True, reset_token=token)
    assert calls[-1][2] == {"dry_run": False, "confirmation": "RESET CAMERAS",
                            "reset_token": token}
    assert done["removed"]["cameras"] == 4
    assert client.get("/api/v1/sources").json() == []


def test_the_mcp_tool_description_marks_it_destructive_and_gates_it_on_user_intent():
    import mcp_server.server as mcp_server

    description = " ".join((mcp_server.reset_cameras.__doc__ or "").split()).lower()
    assert "destructive" in description
    assert "explicitly" in description
    assert "dry run" in description
    for preserved in ("floor plan", "canonical zones"):
        assert preserved in description


# ---------------------------------------------------------------------------
# secrets
# ---------------------------------------------------------------------------

def test_neither_preview_nor_reset_echoes_connection_material(client):
    secret_host = "cam-secret-host.internal"
    client.post("/api/v1/sources", json={
        "name": "Managed cam", "kind": "rtsp", "connection_management": "storelens_managed",
        "connection": {"host": secret_host, "port": 554, "path": "/stream1"}})

    previewed = json.dumps(preview(client))
    assert secret_host not in previewed and "/stream1" not in previewed
    completed = json.dumps(execute(client).json())
    assert secret_host not in completed and "/stream1" not in completed
