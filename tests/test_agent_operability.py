"""Agent-operability regressions built from real StoreLens failures.

Three questions per scenario in evals/agent_operability/scenarios/:

1. does the scenario's own golden path satisfy its rules?
2. does the rule checker actually reject the failures it exists to catch?
3. does executing the golden path against the real platform produce the
   resources the scenario expects — including an alert that fires at 3 and not
   at 2 when the user said "more than 2"?

No language model runs here. These lock in that the curated tools, workflows,
and platform semantics make the correct path available and enforceable; they do
not claim to prove which path a given model will choose (see the evals README).
"""
import json
import os
import sys

import pytest

from server import db
from server.services import alert_engine

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVAL_DIR = os.path.join(ROOT, "evals", "agent_operability")
SCENARIO_DIR = os.path.join(EVAL_DIR, "scenarios")
if EVAL_DIR not in sys.path:
    sys.path.insert(0, EVAL_DIR)

import rules  # noqa: E402

SCENARIO_NAMES = sorted(name[:-5] for name in os.listdir(SCENARIO_DIR) if name.endswith(".json"))
# The 1:1 planar calibration every scenario uses: 100 px = 1 m on a 1000x800 frame,
# so every projected coordinate in this file is checkable by hand.
CALIBRATION = {
    "points": [
        {"px": {"x": 0, "y": 0}, "map": {"x": 0, "y": 0}},
        {"px": {"x": 1000, "y": 0}, "map": {"x": 10, "y": 0}},
        {"px": {"x": 1000, "y": 800}, "map": {"x": 10, "y": 8}},
        {"px": {"x": 0, "y": 800}, "map": {"x": 0, "y": 8}},
    ],
    "frame_w": 1000, "frame_h": 800,
}
AISLE_04_MAP = [{"x": 2, "y": 1}, {"x": 6, "y": 1}, {"x": 6, "y": 7}, {"x": 2, "y": 7}]


def load_scenario(name: str) -> dict:
    with open(os.path.join(SCENARIO_DIR, f"{name}.json"), encoding="utf-8") as handle:
        return json.load(handle)


# ---------------------------------------------------------------------------
# workspace construction
# ---------------------------------------------------------------------------

def build_workspace(client, scenario: dict) -> dict:
    """Realise a scenario's initial_workspace through the real API."""
    spec = scenario["initial_workspace"]
    ids: dict[str, int] = {}
    plan = spec.get("map") or {}
    client.put("/api/v1/store", json={
        "name": "Warehouse", "width_m": plan.get("width_m", 12), "height_m": plan.get("height_m", 10),
        "map": {"walls": [[{"x": 0, "y": 0}, {"x": plan.get("width_m", 12), "y": 0}]]}
        if plan.get("walls") else {},
    })
    for source in spec["sources"]:
        response = client.post("/api/v1/sources", json={"name": source["key"], "kind": "http"})
        assert response.status_code == 201, response.text
        source_id = response.json()["id"]
        ids[f"${source['key']}"] = source_id
        if source.get("calibrated"):
            assert client.put(f"/api/v1/sources/{source_id}/calibration",
                              json=CALIBRATION).status_code == 200
    group_spec = spec.get("multiview_group")
    if group_spec:
        response = client.post("/api/v1/multiview/groups", json={
            "name": group_spec["key"],
            "source_ids": [ids[f"${key}"] for key in group_spec["sources"]],
            "time_tolerance_s": 0.5, "spatial_gate_m": 1.0, "track_age_s": 30,
        })
        assert response.status_code == 201, response.text
        ids["$group"] = response.json()["id"]
    for zone_name in spec.get("zones") or []:
        response = client.post("/api/v1/zones", json={
            "name": zone_name, "ztype": "aisle", "polygon": AISLE_04_MAP})
        assert response.status_code == 201, response.text
        ids["$zone"] = response.json()["id"]
    return ids


def submit_sample(client, source_id, sample_id, ts, people, entity_type="person"):
    """One atomic complete sample. `people` are (entity_id, map_x, map_y) triples."""
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


def occupy(client, ids, source_keys, count, ts=None, prefix="s"):
    """Put `count` distinct fused people inside Aisle 04, seen by every listed source."""
    ts = db.now() if ts is None else ts
    people = [(f"{prefix}-p{index}", 3.0, 1.5 + index * 2.0) for index in range(count)]
    for key in source_keys:
        submit_sample(client, ids[f"${key}"], f"{prefix}-{key}-{ts:.3f}", ts,
                      [(f"{entity}-{key}", x, y) for entity, x, y in people])
    return ts


def _evaluate(client):
    """Run the periodic alert pass the way app.py's poll loop does."""
    zone_names = {zone["id"]: zone["name"] for zone in client.get("/api/v1/zones").json()}
    return alert_engine.evaluate_ongoing(db.now(), zone_names)


def seed_worker(client, ids, source_keys, job_name="Person tracking"):
    """A registered job and a heartbeating worker, the way a real worker registers itself."""
    job = client.post("/api/v1/jobs", json={
        "name": job_name, "description": "local YOLO + ByteTrack",
        "source_ids": [ids[f"${key}"] for key in source_keys],
        "event_types": ["detection"],
    }).json()
    worker = client.post("/api/v1/workers", json={
        "job_id": job["id"], "name": "person-tracker", "version": "1"}).json()
    client.post(f"/api/v1/workers/{worker['id']}/heartbeat", json={
        "status": "running", "metrics": {"local_fps": 35.0, "submission_hz": 10.0}})
    return job, worker


# ---------------------------------------------------------------------------
# 1. every scenario's golden path satisfies its own rules
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", SCENARIO_NAMES)
def test_reference_transcript_satisfies_its_own_scenario(name):
    scenario = load_scenario(name)
    violations = rules.check(scenario, scenario["reference_transcript"])
    assert violations == [], [violation.as_dict() for violation in violations]


@pytest.mark.parametrize("name", SCENARIO_NAMES)
def test_scenario_declares_what_it_guards(name):
    scenario = load_scenario(name)
    assert scenario["what_it_guards"], "a scenario must say which failure it prevents"
    assert scenario["turns"], "a scenario needs at least one user turn"
    assert scenario["expected_final_resources"], "a scenario must assert an end state"


# ---------------------------------------------------------------------------
# 2. the checker rejects the failures it exists to catch
# ---------------------------------------------------------------------------

def _aisle_steps():
    return [dict(step) for step in
            load_scenario("aisle-04-occupancy-alert")["reference_transcript"]["steps"]]


def test_asking_for_coordinates_before_looking_at_a_camera_fails():
    scenario = load_scenario("aisle-04-occupancy-alert")
    transcript = {"steps": [
        {"turn": 0, "action": "inspect_workspace", "args": {}},
        {"turn": 0, "action": "ask_user", "args": {"question": "What polygon is Aisle 04?"}},
    ]}
    kinds = {violation.rule for violation in rules.check(scenario, transcript)}
    assert "order_required" in kinds, "plan_frame_capture must precede the first ask_user"
    assert "actions_required" in kinds


def test_committing_geometry_before_approval_fails():
    scenario = load_scenario("aisle-04-occupancy-alert")
    steps = _aisle_steps()
    early = next(step for step in steps if step["action"] == "commit_zone")
    transcript = {"steps": [step for step in steps if step["turn"] == 0] + [{**early, "turn": 0}]}
    violations = rules.check(scenario, transcript)
    assert any(violation.rule == "actions_forbidden" and violation.turn == 0
               for violation in violations)


def test_keeping_shelving_after_a_floor_only_correction_fails():
    scenario = load_scenario("aisle-04-occupancy-alert")
    steps = _aisle_steps()
    generous = next(step for step in steps
                    if step["action"] == "preview_zone" and step["turn"] == 0)
    transcript = {"steps": [step for step in steps if step["turn"] != 1]
                  + [{**generous, "turn": 1},
                     {"turn": 1, "action": "ask_user", "args": {}}]}
    violations = rules.check(scenario, transcript)
    assert any(violation.rule == "polygon_excludes" for violation in violations), \
        "a polygon still covering shelving must be rejected"


def test_wrong_operator_fails_the_more_than_scenario():
    scenario = load_scenario("more-than-two-operator")
    steps = [dict(step) for step in scenario["reference_transcript"]["steps"]]
    for step in steps:
        if step["action"] == "configure_alert":
            step["args"] = {**step["args"], "operator": ">="}
    violations = rules.check(scenario, {"steps": steps})
    assert [violation.rule for violation in violations] == ["action_arguments"]


def test_starting_a_worker_when_perception_is_healthy_fails():
    scenario = load_scenario("reuse-existing-perception")
    steps = scenario["reference_transcript"]["steps"] + [
        {"turn": 0, "action": "get_worker_recipe", "args": {}},
        {"turn": 0, "action": "run_worker", "args": {"purpose": "start a second tracker"}},
    ]
    kinds = [violation.rule for violation in rules.check(scenario, {"steps": steps})]
    assert "actions_forbidden" in kinds and "actions_forbidden_overall" in kinds


def test_skipping_post_start_verification_fails():
    scenario = load_scenario("aisle-04-occupancy-alert")
    steps = _aisle_steps()
    seen = 0
    trimmed = []
    for step in steps:
        if step["turn"] == 4 and step["action"] == "inspect_perception":
            seen += 1
            if seen == 2:
                continue
        trimmed.append(step)
    violations = rules.check(scenario, {"steps": trimmed})
    assert any(violation.rule == "action_counts_min" for violation in violations)


def test_running_a_worker_before_reading_the_current_recipe_fails():
    scenario = load_scenario("aisle-04-occupancy-alert")
    steps = _aisle_steps()
    reordered = [step for step in steps if not (step["turn"] == 4
                                                and step["action"] == "get_worker_recipe")]
    index = next(i for i, step in enumerate(reordered)
                 if step["turn"] == 4 and step["action"] == "run_worker")
    reordered.insert(index + 1, {"turn": 4, "action": "get_worker_recipe", "args": {}})
    violations = rules.check(scenario, {"steps": reordered})
    assert any(violation.rule == "order_required" for violation in violations)


def test_a_clean_transcript_produces_no_violations_for_every_scenario():
    """Guard against a checker that fails everything as easily as one that passes."""
    for name in SCENARIO_NAMES:
        scenario = load_scenario(name)
        assert rules.check(scenario, scenario["reference_transcript"]) == []


# ---------------------------------------------------------------------------
# 3. executing the golden path against the real platform
# ---------------------------------------------------------------------------

def test_aisle_04_scenario_executes_to_the_expected_resources(client):
    """The real conversation, end to end, through the curated endpoints."""
    scenario = load_scenario("aisle-04-occupancy-alert")
    ids = build_workspace(client, scenario)
    steps = scenario["reference_transcript"]["steps"]

    def views(step):
        return [{**view, "source_id": ids[view["source_id"]]}
                for view in step["args"]["views"]]

    # Turn 0: inspect, look at the cameras, propose, and persist nothing.
    workspace = client.get("/api/v1/agent/workspace").json()
    assert workspace["geometry"]["zones"] == []
    assert workspace["readiness"]["zones"] == "missing"
    assert any("define-zone-from-cameras" in step for step in workspace["next_steps"])
    assert {item["name"] for item in client.get("/api/v1/agent/workflows").json()["workflows"]} \
        >= {"define-zone-from-cameras", "create-zone-occupancy-alert", "run-person-tracking"}
    for key in ("Camera 1", "Camera 2", "Camera 3", "Camera 4"):
        plan = client.get(f"/api/v1/agent/sources/{ids['$' + key]}/frame-capture-plan").json()
        assert plan["executed_by"] == "caller"
        assert plan["geometry_context"]["frame_size"] == {"width": 1000, "height": 800}

    for step in steps:
        if step["action"] != "preview_zone":
            continue
        preview = client.post("/api/v1/agent/zone-preview", json={
            "zone_name": "Aisle 04", "views": views(step)})
        assert preview.status_code == 200, preview.text
        body = preview.json()
        assert body["persisted"] is False
        assert body["canonical_preview"]["component_count"] == 1
        assert client.get("/api/v1/zones").json() == [], "preview must never persist"

    # Turn 3: approval, then one canonical zone.
    commit_step = next(step for step in steps if step["action"] == "commit_zone")
    refused = client.post("/api/v1/agent/zone-commit", json={
        "zone_name": "Aisle 04", "views": views(commit_step)})
    assert refused.status_code == 422, "commit without approval must be refused"

    commit = client.post("/api/v1/agent/zone-commit", json={
        "approved": True, "zone_name": "Aisle 04", "ztype": "aisle",
        "views": views(commit_step)})
    assert commit.status_code == 201, commit.text
    committed = commit.json()
    zone_id = committed["zone"]["id"]
    ids["$zone"] = zone_id

    zones = client.get("/api/v1/zones").json()
    assert len(zones) == 1, "one physical region is one canonical zone"
    assert committed["zone"]["component_count"] == 1
    view_sources = {view["source_id"] for view in client.get("/api/v1/zone-views").json()}
    assert view_sources == {ids["$Camera 3"], ids["$Camera 4"]}, \
        "only the cameras that see the aisle get a zone view"
    assert len(committed["geometry_provenance"]) == 2

    # The committed footprint must be floor, not shelving: px 600 on Camera 3 is map x=6.
    from server.services import zone_geometry
    geometry = committed["zone"]["geometry"]
    assert zone_geometry.contains(geometry, 3.0, 3.0)
    assert not zone_geometry.contains(geometry, 6.5, 3.0), "shelving must stay outside"

    # Perception is missing before the worker runs, and that is reported as unknown.
    perception = client.get("/api/v1/agent/perception", params={
        "source_ids": f"{ids['$Camera 3']},{ids['$Camera 4']}"}).json()
    assert perception["capability"]["state"] == "unavailable"
    assert perception["capability"]["action"] == "perception_missing"
    assert perception["capability"]["no_fresh_sample_meaning"] == "unknown or stale, never zero"

    preview_result = client.post("/api/v1/analytics/query", json={
        "subject": "fused_entity", "measures": ["current_occupancy"],
        "filters": {"group_ids": [ids["$group"]], "zone_ids": [zone_id],
                    "entity_types": ["person"]},
    }).json()
    assert preview_result["rows"][0]["quality"] == "unknown"

    query = client.post("/api/v1/queries", json={
        "name": "People in Aisle 04", "question": "How many people are in Aisle 04 right now?",
        "subject": "fused_entity", "measures": ["current_occupancy"],
        "filters": {"group_ids": [ids["$group"]], "zone_ids": [zone_id],
                    "entity_types": ["person"]}, "created_by": "agent",
    })
    assert query.status_code == 201, query.text
    query_id = query.json()["id"]

    rule = client.post("/api/v1/alert-rules", json={
        "name": "More than 2 people in Aisle 04", "kind": "query_condition",
        "params": {"query_id": query_id},
        "condition": {"operator": ">", "value": 2, "for_seconds": 0},
    })
    assert rule.status_code == 201, rule.text

    # Turn 4: the worker the agent started, then verification.
    cameras = ["Camera 1", "Camera 2", "Camera 3", "Camera 4"]
    seed_worker(client, ids, cameras)
    sample = submit_sample(client, ids["$Camera 3"], "verify-1", db.now(),
                           [("c3-a", 3.0, 2.0)])
    assert sample["sample_status"] == "completed"
    empty = submit_sample(client, ids["$Camera 3"], "verify-empty", db.now(), [])
    assert empty["sample_status"] == "completed" and empty["detection_count"] == 0

    # Every group member reports. Cameras 1 and 2 see nobody in the aisle, and a
    # complete empty sample is how they say so — that is what makes the group's
    # quality `known` rather than `partial`.
    ts = occupy(client, ids, ["Camera 3", "Camera 4"], 3, prefix="run")
    for key in ("Camera 1", "Camera 2"):
        submit_sample(client, ids[f"${key}"], f"run-{key}-empty", ts, [])

    verified = client.get("/api/v1/agent/perception", params={
        "source_ids": ",".join(str(ids[f"${key}"]) for key in cameras)}).json()
    assert verified["capability"]["state"] == "healthy"
    assert verified["capability"]["action"] == "reuse"
    assert verified["multiview"]["ready"] is True
    assert verified["multiview"]["groups"][0]["quality"] == "known"
    assert verified["compatible_jobs"], "the running job must be discoverable for reuse"
    by_name = {item["name"]: item for item in verified["sources"]}
    assert by_name["Camera 3"]["tracking"] is True
    assert by_name["Camera 1"]["current_sample_empty"] is True
    assert by_name["Camera 1"]["last_detection_count"] == 0
    assert by_name["Camera 1"]["state"] == "healthy", \
        "a complete empty sample is an observed zero, not a broken camera"
    assert any("observed zero" in reason for reason in verified["reasons"])

    observed = {
        "zone_count": len(zones),
        "zone_name": committed["zone"]["name"],
        "zone_component_count": committed["zone"]["component_count"],
        "zone_view_source_keys": ["Camera 3", "Camera 4"],
        "saved_query_count": len(client.get("/api/v1/queries").json()),
        "saved_query_subject": "fused_entity",
        "saved_query_measures": ["current_occupancy"],
        "alert_rule_count": len(client.get("/api/v1/alert-rules").json()),
        "alert_rule_kind": rule.json()["kind"],
        "alert_operator": rule.json()["condition"]["operator"],
        "alert_value": rule.json()["condition"]["value"],
        "alert_references_saved_query": rule.json()["params"]["query_id"] == query_id,
        "detection_contract": "detection_sample",
    }
    violations = rules.check_final_resources(scenario, observed)
    assert violations == [], [violation.as_dict() for violation in violations]

    executed = client.post(f"/api/v1/queries/{query_id}/execute").json()
    assert executed["rows"][0]["current_occupancy"] == 3
    assert executed["rows"][0]["quality"] == "known"


@pytest.mark.parametrize("scenario_name,operator,fires_at,quiet_at", [
    ("more-than-two-operator", ">", 3, 2),
    ("at-least-two-operator", ">=", 2, 1),
])
def test_threshold_phrasing_is_never_normalized(client, scenario_name, operator,
                                                fires_at, quiet_at):
    """'More than 2' fires at 3, not at 2. 'At least 2' fires at 2."""
    scenario = load_scenario(scenario_name)
    ids = build_workspace(client, scenario)
    seed_worker(client, ids, ["Camera 3", "Camera 4"])
    query_id = client.post("/api/v1/queries", json={
        "name": "People in Aisle 04", "subject": "fused_entity",
        "measures": ["current_occupancy"],
        "filters": {"group_ids": [ids["$group"]], "zone_ids": [ids["$zone"]],
                    "entity_types": ["person"]}, "created_by": "agent",
    }).json()["id"]
    rule = client.post("/api/v1/alert-rules", json={
        "name": scenario["turns"][0]["user"], "kind": "query_condition",
        "params": {"query_id": query_id},
        "condition": {"operator": operator, "value": 2, "for_seconds": 0},
        "cooldown_s": 0,
    }).json()
    assert rule["condition"]["operator"] == operator, "the stored operator is the user's"

    occupy(client, ids, ["Camera 3", "Camera 4"], quiet_at, prefix="quiet")
    _evaluate(client)
    assert client.get("/api/v1/alerts").json() == [], \
        f"{operator} 2 must not fire at {quiet_at}"

    occupy(client, ids, ["Camera 3", "Camera 4"], fires_at, prefix="fire")
    _evaluate(client)
    alerts = client.get("/api/v1/alerts").json()
    assert len(alerts) == 1, f"{operator} 2 must fire at {fires_at}"
    assert alerts[0]["payload"]["value"] == fires_at
    assert alerts[0]["payload"]["quality"] == "known"

    observed = {"alert_operator": operator, "alert_value": 2,
                f"fires_at_occupancy_{fires_at}": True,
                f"fires_at_occupancy_{quiet_at}": False}
    assert rules.check_final_resources(scenario, observed) == []


def test_every_comparison_operator_is_supported_exactly(client):
    """>, >=, <, <=, == each behave as written, with no silent substitution."""
    scenario = load_scenario("more-than-two-operator")
    ids = build_workspace(client, scenario)
    seed_worker(client, ids, ["Camera 3", "Camera 4"])
    query_id = client.post("/api/v1/queries", json={
        "name": "People in Aisle 04", "subject": "fused_entity",
        "measures": ["current_occupancy"],
        "filters": {"group_ids": [ids["$group"]], "zone_ids": [ids["$zone"]],
                    "entity_types": ["person"]}, "created_by": "agent",
    }).json()["id"]
    cases = [(">", 2, False), (">=", 2, True), ("<", 3, True), ("<=", 3, True), ("==", 2, True)]
    rule_ids = {}
    for operator, value, _ in cases:
        rule_ids[operator] = client.post("/api/v1/alert-rules", json={
            "name": f"occupancy {operator} {value}", "kind": "query_condition",
            "params": {"query_id": query_id},
            "condition": {"operator": operator, "value": value, "for_seconds": 0},
            "cooldown_s": 0,
        }).json()["id"]

    occupy(client, ids, ["Camera 3", "Camera 4"], 2, prefix="two")
    _evaluate(client)
    fired = {alert["rule_id"] for alert in client.get("/api/v1/alerts").json()}
    for operator, value, should_fire in cases:
        assert (rule_ids[operator] in fired) is should_fire, \
            f"occupancy 2 {operator} {value} should be {should_fire}"


def test_missing_entity_capability_is_reported_not_substituted(client):
    scenario = load_scenario("missing-entity-capability")
    ids = build_workspace(client, scenario)
    seed_worker(client, ids, ["Camera 3", "Camera 4"])
    occupy(client, ids, ["Camera 3", "Camera 4"], 2, prefix="people")

    source_ids = f"{ids['$Camera 3']},{ids['$Camera 4']}"
    person = client.get("/api/v1/agent/perception", params={"source_ids": source_ids}).json()
    backpack = client.get("/api/v1/agent/perception", params={
        "entity_type": "backpack", "source_ids": source_ids}).json()

    observed = {
        "backpack_capability_state": backpack["capability"]["state"],
        "backpack_action": backpack["capability"]["action"],
        "person_capability_state": person["capability"]["state"],
        "saved_query_count": len(client.get("/api/v1/queries").json()),
        "alert_rule_count": len(client.get("/api/v1/alert-rules").json()),
    }
    violations = rules.check_final_resources(scenario, observed)
    assert violations == [], [violation.as_dict() for violation in violations]
    assert backpack["observed_entity_types"] == ["person"], \
        "the response must show what does exist, so the gap is actionable"
    assert any("backpack" in reason for reason in backpack["reasons"])


def test_a_stale_required_camera_is_partial_never_zero(client, monkeypatch):
    scenario = load_scenario("stale-camera-quality")
    ids = build_workspace(client, scenario)
    seed_worker(client, ids, ["Camera 3", "Camera 4"])

    # Camera 4's only sample is older than the group's track age; Camera 3 is current.
    now = db.now()
    submit_sample(client, ids["$Camera 4"], "c4-old", now - 120, [("c4-a", 3.0, 2.0)])
    submit_sample(client, ids["$Camera 3"], "c3-now", now, [("c3-a", 3.0, 2.0)])

    source_ids = f"{ids['$Camera 3']},{ids['$Camera 4']}"
    perception = client.get("/api/v1/agent/perception", params={"source_ids": source_ids}).json()
    result = client.post("/api/v1/analytics/query", json={
        "subject": "fused_entity", "measures": ["current_occupancy"],
        "filters": {"group_ids": [ids["$group"]], "zone_ids": [ids["$zone"]],
                    "entity_types": ["person"]},
    }).json()

    stale_names = {item["name"] for item in perception["sources"] if item["state"] == "stale"}
    observed = {
        "perception_state": perception["capability"]["state"],
        "perception_action": perception["capability"]["action"],
        "group_quality": perception["multiview"]["groups"][0]["quality"],
        "query_quality": result["rows"][0]["quality"],
        "stale_source_keys": sorted(stale_names),
    }
    violations = rules.check_final_resources(scenario, observed)
    assert violations == [], [violation.as_dict() for violation in violations]
    assert result["rows"][0]["current_occupancy"] != 0, \
        "a stale camera must not turn into an observed zero"
    assert any("never as zero" in reason for reason in perception["reasons"])


def test_healthy_perception_is_reused_rather_than_duplicated(client):
    scenario = load_scenario("reuse-existing-perception")
    ids = build_workspace(client, scenario)
    job, _ = seed_worker(client, ids, ["Camera 3", "Camera 4"])
    occupy(client, ids, ["Camera 3", "Camera 4"], 2, prefix="reuse")

    perception = client.get("/api/v1/agent/perception", params={
        "source_ids": f"{ids['$Camera 3']},{ids['$Camera 4']}"}).json()
    result = client.post("/api/v1/analytics/query", json={
        "subject": "fused_entity", "measures": ["current_occupancy"],
        "filters": {"group_ids": [ids["$group"]], "zone_ids": [ids["$zone"]],
                    "entity_types": ["person"]},
    }).json()
    observed = {
        "perception_action": perception["capability"]["action"],
        "query_subject": "fused_entity",
        "query_quality": result["rows"][0]["quality"],
        "job_count": len(client.get("/api/v1/jobs").json()),
    }
    violations = rules.check_final_resources(scenario, observed)
    assert violations == [], [violation.as_dict() for violation in violations]
    assert perception["compatible_jobs"][0]["job_id"] == job["id"]
    # Two cameras, one person each, one physical person: fusion, not addition.
    assert result["rows"][0]["current_occupancy"] == 2
