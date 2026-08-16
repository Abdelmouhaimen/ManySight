"""The curated agent operating surface: one snapshot, one capability check, one
recipe, one safe zone workflow, one workflow index.

These are the endpoints that exist so an agent does not have to reconstruct
ManySight architecture from low-level API trial and error, so the tests care
about two things a normal router test would not: that the answers are *complete*
enough to act on, and that they never leak connection material.
"""
import json
import os

import pytest

from server import db
from server.routers import agent_ops
from server.services import agent_workflows

CALIBRATION = {
    "points": [
        {"px": {"x": 0, "y": 0}, "map": {"x": 0, "y": 0}},
        {"px": {"x": 1000, "y": 0}, "map": {"x": 10, "y": 0}},
        {"px": {"x": 1000, "y": 800}, "map": {"x": 10, "y": 8}},
        {"px": {"x": 0, "y": 800}, "map": {"x": 0, "y": 8}},
    ],
    "frame_w": 1000, "frame_h": 800,
}
FLOOR_PX = [{"x": 200, "y": 100}, {"x": 500, "y": 100}, {"x": 500, "y": 700}, {"x": 200, "y": 700}]
OVERLAP_PX = [{"x": 400, "y": 150}, {"x": 700, "y": 150}, {"x": 700, "y": 650}, {"x": 400, "y": 650}]


def make_source(client, name, calibrated=True, kind="http"):
    source_id = client.post("/api/v1/sources", json={"name": name, "kind": kind}).json()["id"]
    if calibrated:
        assert client.put(f"/api/v1/sources/{source_id}/calibration",
                          json=CALIBRATION).status_code == 200
    return source_id


def submit_sample(client, source_id, sample_id, ts, people, entity_type="person"):
    response = client.post("/api/v1/detection-samples", json={
        "schema_version": 2, "source_id": source_id, "sample_id": sample_id,
        "timestamp": ts, "entity_type": entity_type,
        "detections": [{
            "entity_id": entity_id, "label": entity_type, "confidence": 0.9,
            "point_px": [x * 100, y * 100],
            "bbox_px": [x * 100 - 20, y * 100 - 80, x * 100 + 20, y * 100],
        } for entity_id, x, y in people],
    })
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------------------
# inspect_workspace
# ---------------------------------------------------------------------------

def test_workspace_snapshot_answers_readiness_in_one_call(client):
    first = make_source(client, "Camera 3")
    second = make_source(client, "Camera 4")
    uncalibrated = make_source(client, "Camera 5", calibrated=False)
    client.put("/api/v1/store", json={"name": "Warehouse", "width_m": 12, "height_m": 10,
                                      "map": {"walls": [[{"x": 0, "y": 0}, {"x": 12, "y": 0}]]}})
    zone = client.post("/api/v1/zones", json={
        "name": "Aisle 04", "ztype": "aisle",
        "polygon": [{"x": 2, "y": 1}, {"x": 6, "y": 1}, {"x": 6, "y": 7}, {"x": 2, "y": 7}]}).json()
    client.post("/api/v1/zone-views", json={
        "zone_id": zone["id"], "source_id": first, "outer_polygon_px": FLOOR_PX})
    group = client.post("/api/v1/multiview/groups", json={
        "name": "Warehouse floor", "source_ids": [first, second], "track_age_s": 30}).json()
    query = client.post("/api/v1/queries", json={
        "name": "People in Aisle 04", "subject": "fused_entity",
        "measures": ["current_occupancy"],
        "filters": {"group_ids": [group["id"]], "zone_ids": [zone["id"]]}}).json()
    client.post("/api/v1/alert-rules", json={
        "name": "Crowded", "kind": "query_condition", "params": {"query_id": query["id"]},
        "condition": {"operator": ">", "value": 2}})

    body = client.get("/api/v1/agent/workspace").json()

    assert set(body) == {"workspace", "sources", "geometry", "perception", "multiview",
                         "analytics", "readiness", "next_steps"}
    assert body["workspace"]["map_ready"] is True
    assert body["workspace"]["space_revision_id"] == db.current_space_revision_id()
    assert body["workspace"]["space_revision_number"] == 1

    by_name = {item["name"]: item for item in body["sources"]}
    assert by_name["Camera 3"]["calibrated"] is True
    assert by_name["Camera 3"]["zone_view_count"] == 1
    assert by_name["Camera 5"]["calibrated"] is False
    assert by_name["Camera 3"]["observation_state"] == "unavailable"

    assert body["geometry"]["calibrated_source_ids"] == [first, second]
    assert body["geometry"]["uncalibrated_source_ids"] == [uncalibrated]
    assert body["geometry"]["zones"][0]["name"] == "Aisle 04"
    assert body["geometry"]["zones"][0]["zone_view_source_ids"] == [first]

    assert body["multiview"]["groups"][0]["quality"] == "unknown"
    assert body["multiview"]["groups"][0]["stale_source_ids"] == [first, second]

    assert body["analytics"]["saved_queries"][0]["subject"] == "fused_entity"
    assert body["analytics"]["alert_rules"][0]["condition"]["operator"] == ">"
    assert "fused_entity" in body["analytics"]["query_capabilities"]["subjects"]

    assert body["readiness"] == {"map": "ready", "calibration": "partial", "zones": "ready",
                                 "perception": "missing", "multiview": "partial"}
    assert any(str(uncalibrated) in step for step in body["next_steps"])


def test_workspace_snapshot_never_contains_connection_material(client):
    """A snapshot is safe to paste into a conversation; connection details are not."""
    secret_host = "cam-secret-host.internal"
    source_id = client.post("/api/v1/sources", json={
        "name": "Managed cam", "kind": "rtsp", "connection_management": "manysight_managed",
        "connection": {"host": secret_host, "port": 554, "path": "/stream1"},
    }).json()["id"]

    for path in ("/api/v1/agent/workspace",
                 f"/api/v1/agent/sources/{source_id}",
                 f"/api/v1/agent/sources/{source_id}/frame-capture-plan",
                 "/api/v1/agent/perception"):
        payload = json.dumps(client.get(path).json())
        assert secret_host not in payload, f"{path} leaked connection material"
        assert "/stream1" not in payload, f"{path} leaked connection material"
        assert "554" not in payload, f"{path} leaked connection material"

    detail = client.get(f"/api/v1/agent/sources/{source_id}").json()
    assert detail["connection"] == {
        "configured": True, "management": "manysight_managed", "mode": "agent_local",
        "revision": 1, "credential_status": "absent",
        "resolution": "Call get_source_connection only inside an authorized local worker.",
    }


def test_workspace_snapshot_stays_bounded_on_a_busy_workspace(client):
    """Cheap and concise: no unbounded list, and no observation table scan."""
    source_id = make_source(client, "Camera 3")
    now = db.now()
    for index in range(60):
        client.post("/api/v1/zones", json={
            "name": f"Zone {index}", "ztype": "area",
            "polygon": [{"x": 0, "y": 0}, {"x": 1, "y": 0}, {"x": 1, "y": 1}]})
    for index in range(40):
        submit_sample(client, source_id, f"s{index}", now - 600 + index, [("a", 3.0, 2.0)])

    body = client.get("/api/v1/agent/workspace").json()
    assert len(body["geometry"]["zones"]) == agent_ops.MAX_LISTED
    for key in ("saved_queries", "dashboards", "alert_rules"):
        assert len(body["analytics"][key]) <= agent_ops.MAX_LISTED
    for key, value in body["analytics"]["query_capabilities"].items():
        if isinstance(value, list):
            assert len(value) <= agent_ops.MAX_LISTED, key
    assert len(json.dumps(body)) < 60_000, "a snapshot must fit comfortably in context"


# ---------------------------------------------------------------------------
# inspect_source and the frame capture plan
# ---------------------------------------------------------------------------

def test_source_detail_covers_calibration_geometry_and_evidence(client):
    source_id = make_source(client, "Camera 3")
    zone = client.post("/api/v1/zones", json={
        "name": "Aisle 04", "ztype": "aisle",
        "polygon": [{"x": 2, "y": 1}, {"x": 6, "y": 1}, {"x": 6, "y": 7}, {"x": 2, "y": 7}]}).json()
    client.post("/api/v1/zone-views", json={
        "zone_id": zone["id"], "source_id": source_id, "outer_polygon_px": FLOOR_PX})
    now = db.now()
    for index in range(6):
        submit_sample(client, source_id, f"s{index}", now - 1 + index * 0.2, [("a", 3.0, 2.0)])

    body = client.get(f"/api/v1/agent/sources/{source_id}").json()
    assert body["calibration"]["floor_homography"] is True
    assert body["calibration"]["frame_size"] == {"width": 1000, "height": 800}
    assert body["geometry"]["zone_views"][0]["zone_name"] == "Aisle 04"
    assert body["perception"]["has_complete_sample"] is True
    assert body["perception"]["state"] == "healthy"
    assert body["perception"]["evidence"]["tracking"] is True
    assert body["perception"]["evidence"]["bbox_px"] is True
    assert body["perception"]["submission_hz"] and body["perception"]["submission_hz"] > 0
    assert client.get("/api/v1/agent/sources/9999").status_code == 404


def test_frame_capture_plan_is_local_and_never_returns_pixels(client):
    source_id = make_source(client, "Camera 3")
    body = client.get(f"/api/v1/agent/sources/{source_id}/frame-capture-plan").json()
    assert body["executed_by"] == "caller"
    assert "open_capture" in body["sdk"]["helper"]
    assert body["geometry_context"]["calibrated"] is True
    assert any("never" in item.lower() for item in body["safety"])
    # No pixels, encoded or otherwise: every value is short prose or a plan line.
    assert "base64" not in json.dumps(body)
    assert not {"image", "frame", "jpeg", "data"} & set(body)
    for value in body.values():
        assert len(json.dumps(value)) < 2000


# ---------------------------------------------------------------------------
# inspect_perception
# ---------------------------------------------------------------------------

def test_perception_reports_missing_capability_without_guessing(client):
    source_id = make_source(client, "Camera 3")
    body = client.get("/api/v1/agent/perception").json()
    assert body["capability"]["state"] == "unavailable"
    assert body["capability"]["action"] == "perception_missing"
    assert body["capability"]["unavailable_source_ids"] == [source_id]
    assert body["capability"]["no_fresh_sample_meaning"] == "unknown or stale, never zero"
    assert "get_worker_recipe" in body["next"]
    assert body["observed_entity_types"] == []


def test_perception_reports_healthy_capability_with_rate_and_worker(client):
    source_id = make_source(client, "Camera 3")
    job = client.post("/api/v1/jobs", json={
        "name": "Person tracking", "source_ids": [source_id]}).json()
    worker = client.post("/api/v1/workers", json={"job_id": job["id"], "name": "yolo"}).json()
    client.post(f"/api/v1/workers/{worker['id']}/heartbeat", json={
        "status": "running", "metrics": {"processing_fps": 35.0, "submission_hz": 10.0}})
    now = db.now()
    for index in range(11):
        submit_sample(client, source_id, f"s{index}", now - 1 + index * 0.1, [("a", 3.0, 2.0)])

    body = client.get("/api/v1/agent/perception").json()
    assert body["capability"]["state"] == "healthy"
    assert body["capability"]["action"] == "reuse"
    assert "do not start another worker" in body["next"]
    item = body["sources"][0]
    assert item["tracking"] is True
    assert item["processing_fps"] == 35.0
    assert item["submission_hz"] is not None
    assert item["worker"]["effective_status"] == "running"
    assert body["compatible_jobs"][0]["job_id"] == job["id"]


def test_perception_distinguishes_stale_from_absent(client):
    fresh = make_source(client, "Camera 3")
    stale = make_source(client, "Camera 4")
    absent = make_source(client, "Camera 5")
    now = db.now()
    submit_sample(client, fresh, "fresh", now, [("a", 3.0, 2.0)])
    submit_sample(client, stale, "stale", now - 600, [("b", 3.0, 2.0)])

    body = client.get("/api/v1/agent/perception").json()
    assert body["capability"]["state"] == "partial"
    assert body["capability"]["action"] == "extend_coverage"
    assert body["capability"]["healthy_source_ids"] == [fresh]
    assert body["capability"]["stale_source_ids"] == [stale]
    assert body["capability"]["unavailable_source_ids"] == [absent]
    assert any("never as zero" in reason for reason in body["reasons"])


def test_perception_treats_a_complete_empty_sample_as_an_observed_zero(client):
    source_id = make_source(client, "Camera 3")
    submit_sample(client, source_id, "empty", db.now(), [])
    body = client.get("/api/v1/agent/perception").json()
    item = body["sources"][0]
    assert item["state"] == "healthy"
    assert item["current_sample_empty"] is True
    assert item["last_detection_count"] == 0
    assert item["tracking"] is None, "an empty frame neither proves nor disproves tracking"
    assert body["capability"]["action"] == "reuse"
    assert any("observed zero" in reason for reason in body["reasons"])


def test_perception_flags_untracked_detections(client):
    source_id = make_source(client, "Camera 3")
    now = db.now()
    client.post("/api/v1/observations/batch", json={"observations": [
        {"schema_version": 2, "observation_id": "d1", "sample_id": "s1", "kind": "detection",
         "timestamp": now, "source_id": source_id, "entity_type": "person",
         "geometry": {"point_px": [300, 200]}},
        {"schema_version": 2, "observation_id": "m1", "sample_id": "s1", "kind": "measurement",
         "timestamp": now, "source_id": source_id, "name": "detection_frame_count",
         "label": "person", "value": 1},
    ]})
    body = client.get("/api/v1/agent/perception").json()
    assert body["sources"][0]["tracking"] is False
    assert body["capability"]["action"] != "reuse"
    assert any("entity_id" in reason for reason in body["reasons"])
    relaxed = client.get("/api/v1/agent/perception", params={"require_tracking": "false"}).json()
    assert relaxed["capability"]["action"] == "reuse"


def test_perception_warns_when_sources_are_not_one_fusion_group(client):
    first = make_source(client, "Camera 3")
    second = make_source(client, "Camera 4")
    body = client.get("/api/v1/agent/perception", params={
        "source_ids": f"{first},{second}"}).json()
    assert any("double-count" in reason for reason in body["reasons"])
    assert body["multiview"]["ready"] is False
    assert client.get("/api/v1/agent/perception",
                      params={"source_ids": "not-an-id"}).status_code == 422
    assert client.get("/api/v1/agent/perception", params={"source_ids": "9999"}).status_code == 404


# ---------------------------------------------------------------------------
# get_worker_recipe
# ---------------------------------------------------------------------------

def test_worker_recipe_is_the_current_contract_not_a_script(client):
    body = client.get("/api/v1/agent/worker-recipe").json()
    assert body["submission"]["preferred_endpoint"] == "POST /api/v1/detection-samples"
    assert body["submission"]["atomic"] is True
    assert "observed zero" in body["submission"]["empty_frame"]
    assert "fake" in body["submission"]["never"]
    assert {"detections", "sample_id", "source_id", "timestamp", "entity_type"} \
        <= set(body["submission"]["envelope_fields"])
    assert body["submission"]["legacy"]["use_when"].startswith("Only for")
    assert body["identity"]["entity_id"].startswith("opaque source-local")
    assert "Never join IDs across sources" in body["identity"]["cross_camera"]
    for forbidden in ("zone_id", "zone_enter", "occupancy", "fused identity"):
        assert forbidden in body["forbidden_worker_output"]
    assert "full camera FPS" in body["sampling"]["principle"]
    assert "no globally correct submission rate" in " ".join(body["sampling"]["guidance"])
    assert set(body["sampling"]["report_in_heartbeat"]) >= {"processing_fps", "submission_hz"}
    assert body["skill"] == "perception-workers"
    assert "Do NOT infer it from an example" in body["authority"]
    assert any("conda" in item or "virtualenv" in item
               for item in body["local_environment"]["order"])
    assert client.get("/api/v1/agent/worker-recipe",
                      params={"source_ids": "1,x"}).status_code == 422


def test_worker_recipe_envelope_fields_track_the_real_model(client):
    """If the DetectionSample model changes, the recipe changes with it."""
    from server.routers.observations import DetectionSampleIn
    body = client.get("/api/v1/agent/worker-recipe").json()
    assert body["submission"]["envelope_fields"] == sorted(DetectionSampleIn.model_fields)


# ---------------------------------------------------------------------------
# zone preview and commit
# ---------------------------------------------------------------------------

def test_preview_projects_two_cameras_without_persisting_anything(client):
    first = make_source(client, "Camera 3")
    second = make_source(client, "Camera 4")
    body = {"zone_name": "Aisle 04", "views": [
        {"source_id": first, "polygon_px": FLOOR_PX},
        {"source_id": second, "polygon_px": OVERLAP_PX}]}

    for _ in range(3):  # repeated preview is always safe
        response = client.post("/api/v1/agent/zone-preview", json=body)
        assert response.status_code == 200, response.text
        preview = response.json()
        assert preview["persisted"] is False
        assert [item["source_id"] for item in preview["views"]] == [first, second]
        assert all(item["valid"] for item in preview["views"])
        # 100 px = 1 m: 200..500 px is 2..5 m, 400..700 px is 4..7 m.
        assert preview["views"][0]["polygon_map"][0] == {"x": 2.0, "y": 1.0}
        assert preview["views"][0]["area_m2"] == 18.0
        assert preview["canonical_preview"]["geometry_type"] == "Polygon"
        assert preview["canonical_preview"]["component_count"] == 1
        assert preview["canonical_preview"]["area_m2"] == 28.0
        assert preview["provenance"]["contributing_source_ids"] == [first, second]

    assert client.get("/api/v1/zones").json() == []
    assert client.get("/api/v1/zone-views").json() == []


def test_preview_reports_an_uncalibrated_camera_instead_of_failing_silently(client):
    calibrated = make_source(client, "Camera 3")
    blind = make_source(client, "Camera 9", calibrated=False)
    preview = client.post("/api/v1/agent/zone-preview", json={"zone_name": "Aisle 04", "views": [
        {"source_id": calibrated, "polygon_px": FLOOR_PX},
        {"source_id": blind, "polygon_px": FLOOR_PX}]}).json()
    invalid = next(item for item in preview["views"] if item["source_id"] == blind)
    assert invalid["valid"] is False
    assert "calibration" in invalid["error"]
    assert any("not calibrated" in warning for warning in preview["warnings"])
    assert preview["canonical_preview"]["component_count"] == 1, "the valid camera still previews"

    refused = client.post("/api/v1/agent/zone-commit", json={
        "approved": True, "zone_name": "Aisle 04", "views": [
            {"source_id": calibrated, "polygon_px": FLOOR_PX},
            {"source_id": blind, "polygon_px": FLOOR_PX}]})
    assert refused.status_code == 422
    assert client.get("/api/v1/zones").json() == []


def test_commit_requires_approval_and_creates_exactly_one_canonical_zone(client):
    first = make_source(client, "Camera 3")
    second = make_source(client, "Camera 4")
    views = [{"source_id": first, "polygon_px": FLOOR_PX},
             {"source_id": second, "polygon_px": OVERLAP_PX}]

    assert client.post("/api/v1/agent/zone-commit", json={
        "zone_name": "Aisle 04", "views": views}).status_code == 422
    assert client.post("/api/v1/agent/zone-commit", json={
        "approved": True, "views": views}).status_code == 422, "a new zone needs a name"
    assert client.get("/api/v1/zones").json() == []

    response = client.post("/api/v1/agent/zone-commit", json={
        "approved": True, "zone_name": "Aisle 04", "ztype": "aisle", "views": views})
    assert response.status_code == 201, response.text
    committed = response.json()

    assert len(client.get("/api/v1/zones").json()) == 1
    assert committed["zone"]["name"] == "Aisle 04"
    assert committed["zone"]["ztype"] == "aisle"
    assert committed["zone"]["component_count"] == 1
    assert committed["zone"]["revision"] == 2, "seed plus one explicit extension"
    assert len(committed["zone_view_ids"]) == 2
    assert [item["source_id"] for item in committed["extensions"]] == [second]

    operations = [item["operation"] for item in committed["geometry_provenance"]]
    assert operations == ["create_from_camera_polygon", "extend_from_zone_view"]
    for item in committed["geometry_provenance"]:
        assert item["original_pixel_polygon"], "pixel evidence is retained"
        assert item["projected_map_polygon"], "the projected contribution is retained"
        assert item["stale"] is False


def test_commit_can_extend_an_existing_canonical_zone(client):
    first = make_source(client, "Camera 3")
    second = make_source(client, "Camera 4")
    created = client.post("/api/v1/agent/zone-commit", json={
        "approved": True, "zone_name": "Aisle 04",
        "views": [{"source_id": first, "polygon_px": FLOOR_PX}]}).json()
    zone_id = created["zone"]["id"]

    preview = client.post("/api/v1/agent/zone-preview", json={
        "zone_id": zone_id, "views": [{"source_id": second, "polygon_px": OVERLAP_PX}]}).json()
    assert preview["zone_name"] == "Aisle 04"
    assert preview["provenance"]["operation"] == "extend existing canonical zone"
    assert preview["canonical_preview"]["area_m2"] == 28.0

    extended = client.post("/api/v1/agent/zone-commit", json={
        "approved": True, "zone_id": zone_id,
        "views": [{"source_id": second, "polygon_px": OVERLAP_PX}]}).json()
    assert len(client.get("/api/v1/zones").json()) == 1, "still one canonical zone"
    assert created["zone"]["revision"] == 1, "a single seed view is the zone's first revision"
    assert extended["zone"]["revision"] == 2, "the second camera is an explicit union"
    assert {view["source_id"] for view in client.get("/api/v1/zone-views").json()} == {first, second}


def test_preview_rejects_unusable_input(client):
    source_id = make_source(client, "Camera 3")
    assert client.post("/api/v1/agent/zone-preview", json={"views": []}).status_code == 422
    assert client.post("/api/v1/agent/zone-preview", json={"views": [
        {"source_id": source_id, "polygon_px": [{"x": 0, "y": 0}, {"x": 1, "y": 1}]}]}
    ).status_code == 422
    assert client.post("/api/v1/agent/zone-preview", json={"views": [
        {"source_id": 9999, "polygon_px": FLOOR_PX}]}).status_code == 404
    assert client.post("/api/v1/agent/zone-preview", json={
        "zone_id": 9999, "views": [{"source_id": source_id, "polygon_px": FLOOR_PX}]}
    ).status_code == 404


# ---------------------------------------------------------------------------
# workflows
# ---------------------------------------------------------------------------

def test_workflow_index_routes_a_goal_to_its_skills(client):
    index = client.get("/api/v1/agent/workflows").json()["workflows"]
    names = {item["name"] for item in index}
    assert names == set(agent_workflows.WORKFLOW_ORDER)
    assert {"define-zone-from-cameras", "create-zone-occupancy-alert", "run-person-tracking",
            "configure-multiview", "create-generated-dashboard", "inspect-source",
            "onboard-camera"} <= names
    for item in index:
        assert item["when"], f"{item['name']} must say when to use it"
        assert item["skills"], f"{item['name']} must name the skills behind it"
    # That those skill files exist is asserted in test_agent_docs.py, which owns
    # skill/doc consistency; this test owns the endpoint's contract.


def test_one_workflow_carries_prerequisites_sequence_and_invariants(client):
    body = client.get("/api/v1/agent/workflows/define-zone-from-cameras").json()
    assert body["name"] == "define-zone-from-cameras"
    assert body["prerequisites"] and body["sequence"] and body["invariants"]
    assert "preview_zone" in body["tools"] and "commit_zone" in body["tools"]
    joined = " ".join(body["sequence"] + body["invariants"]).lower()
    assert "do not ask the user for coordinates" in joined
    assert "never persisted before approval" in joined
    assert "one canonical zone" in joined
    assert client.get("/api/v1/agent/workflows/nope").status_code == 404


def test_the_alert_workflow_publishes_the_exact_operator_table(client):
    body = client.get("/api/v1/agent/workflows/create-zone-occupancy-alert").json()
    table = body["comparison_operators"]
    assert table["more than {n}"] == ">"
    assert table["at least {n}"] == ">="
    assert table["fewer than {n}"] == "<"
    assert table["at most {n}"] == "<="
    assert table["exactly {n}"] == "=="
    invariants = " ".join(body["invariants"])
    assert "'More than 2' is > 2 and 'at least 2' is >= 2" in invariants
    assert "never" in invariants.lower()


@pytest.mark.parametrize("phrase,operator,value", [
    ("more than 2", ">", 2),
    ("over 2", ">", 2),
    ("at least 2", ">=", 2),
    ("2 or more", ">=", 2),
    ("fewer than 3", "<", 3),
    ("less than 3", "<", 3),
    ("at most 3", "<=", 3),
    ("no more than 3", "<=", 3),
    ("exactly 3", "==", 3),
])
def test_threshold_phrases_map_to_exact_operators(phrase, operator, value):
    parsed = agent_workflows.parse_threshold(phrase)
    assert parsed == {"operator": operator, "value": value, "phrase": phrase}


@pytest.mark.parametrize("phrase", ["about 2", "roughly two people", "more than a few", "", "2"])
def test_an_unrecognised_threshold_phrase_is_not_guessed(phrase):
    assert agent_workflows.parse_threshold(phrase) is None


# ---------------------------------------------------------------------------
# additive-migration safety
# ---------------------------------------------------------------------------

def test_agent_surface_works_on_a_migrated_populated_database(uninitialized_db, monkeypatch):
    """No new tables were added for this surface: it derives from existing data.

    Built the way test_migration.py does — an older schema with rows in it,
    brought up to date by init_db() — so a real upgrade path is exercised rather
    than a fresh database.
    """
    import sqlite3
    con = sqlite3.connect(uninitialized_db.DB_PATH)
    con.executescript("""
        CREATE TABLE stores (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL DEFAULT 'My space',
          width_m REAL NOT NULL DEFAULT 20, height_m REAL NOT NULL DEFAULT 12,
          map_json TEXT NOT NULL DEFAULT '{}', created_at REAL);
        CREATE TABLE sources (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
          kind TEXT NOT NULL DEFAULT 'webcam', locator_json TEXT DEFAULT '{}',
          capabilities_json TEXT DEFAULT '[]', metadata_json TEXT DEFAULT '{}',
          map_x REAL, map_y REAL, rotation_deg REAL DEFAULT 0, fov_deg REAL DEFAULT 70,
          calibration_json TEXT, created_at REAL, last_observation_at REAL,
          last_ingestion_at REAL, event_count INTEGER DEFAULT 0);
        CREATE TABLE zones (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
          ztype TEXT DEFAULT 'area', color TEXT DEFAULT '', polygon_json TEXT NOT NULL,
          created_at REAL, updated_at REAL);
        CREATE TABLE jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
          description TEXT DEFAULT '', source_ids TEXT DEFAULT '[]', event_types TEXT DEFAULT '[]',
          status TEXT DEFAULT 'active', created_at REAL, last_event_at REAL,
          event_count INTEGER DEFAULT 0);
        CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, job_id INTEGER, source_id INTEGER,
          ts REAL NOT NULL, event_type TEXT NOT NULL DEFAULT 'detection', track_id TEXT,
          zone_id INTEGER, x_px REAL, y_px REAL, x_map REAL, y_map REAL, value REAL, label TEXT,
          attributes TEXT DEFAULT '{}', created_at REAL);
        INSERT INTO stores (id, name, width_m, height_m, map_json, created_at)
          VALUES (1, 'Legacy store', 20, 12, '{"walls": [[{"x":0,"y":0},{"x":20,"y":0}]]}', 1.0);
        INSERT INTO sources (id, name, kind, created_at) VALUES (1, 'Legacy cam', 'rtsp', 1.0);
        INSERT INTO zones (id, name, ztype, polygon_json, created_at, updated_at)
          VALUES (1, 'Legacy aisle', 'aisle', '[{"x":0,"y":0},{"x":4,"y":0},{"x":4,"y":4}]', 1.0, 1.0);
        INSERT INTO jobs (id, name, source_ids, created_at) VALUES (1, 'Legacy job', '[1]', 1.0);
        INSERT INTO events (job_id, source_id, ts, event_type, track_id, label, created_at)
          VALUES (1, 1, 1.0, 'detection', 't1', 'customer', 1.0);
    """)
    con.commit()
    con.close()
    uninitialized_db.init_db()

    workspace = agent_ops.inspect_workspace()
    assert workspace["workspace"]["name"] == "Legacy store"
    assert workspace["workspace"]["map_ready"] is True
    assert workspace["sources"][0]["name"] == "Legacy cam"
    assert workspace["sources"][0]["calibrated"] is False
    assert workspace["geometry"]["zones"][0]["name"] == "Legacy aisle"
    assert workspace["geometry"]["zones"][0]["geometry_type"] == "Polygon"
    assert workspace["readiness"]["perception"] == "missing"

    perception = agent_ops.inspect_perception()
    assert perception["capability"]["state"] == "unavailable"
    assert perception["sources"][0]["worker"] is None, "a legacy job with no worker row is honest"
    assert agent_ops.worker_recipe()["submission"]["atomic"] is True
