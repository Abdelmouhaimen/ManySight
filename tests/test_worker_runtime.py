"""Tracking frame rate and hardware readiness guidance.

The defect this pins down is quiet: a person tracker configured at 2 FPS submits
perfectly valid samples, passes every availability check, and tracks badly. The
platform therefore has to state a processing floor, keep it distinct from the
submission rate, and score what a worker actually achieved — while never turning
a missing GPU into an unusable camera.

No test here requires an NVIDIA device; capability detection is faked.
"""
from __future__ import annotations

import importlib
import json
import re
import sys

import pytest

from server.services import worker_runtime as runtime
from test_agent_surface import make_source, submit_sample

from server import db


def sdk():
    sys.path.insert(0, "sdk/python")
    return importlib.import_module("manysight")


# ---------------------------------------------------------------------------
# the rate policy itself
# ---------------------------------------------------------------------------

def test_a_capable_source_gets_at_least_the_tracking_floor():
    plan = runtime.rate_plan(source_fps=30.0, tracking=True)
    assert plan["tracking_enabled"] is True
    assert plan["minimum_processing_fps"] == 15.0
    assert plan["target_processing_fps"] >= 15.0
    assert plan["target_processing_fps"] == 30.0
    assert plan["source_limited"] is False


@pytest.mark.parametrize("source_fps", [15.0, 16.0, 25.0, 30.0, 60.0, 120.0])
def test_no_capable_source_is_ever_recommended_below_the_floor(source_fps):
    plan = runtime.rate_plan(source_fps=source_fps, tracking=True)
    assert plan["target_processing_fps"] >= runtime.TRACKING_MIN_PROCESSING_FPS
    # And never faster than the source can actually deliver.
    assert plan["target_processing_fps"] <= source_fps


def test_a_fast_source_is_capped_at_the_preferred_rate_but_may_go_native():
    plan = runtime.rate_plan(source_fps=60.0, tracking=True)
    assert plan["target_processing_fps"] == runtime.TRACKING_PREFERRED_PROCESSING_FPS
    assert plan["may_increase_to_fps"] == 60.0, "source-native stays available, not mandated"


def test_a_slow_source_gets_its_native_rate_not_an_impossible_recommendation():
    """The floor is a target, not a fiction. A 6 FPS camera cannot reach 15."""
    plan = runtime.rate_plan(source_fps=6.0, tracking=True)
    assert plan["target_processing_fps"] == 6.0
    assert plan["source_limited"] is True
    assert plan["minimum_processing_fps"] == 15.0, "the floor is still stated, just unmet"
    assert any("below the 15 FPS tracking floor" in line for line in plan["rationale"])


def test_an_unknown_source_rate_falls_back_to_the_floor_and_says_so():
    plan = runtime.rate_plan(source_fps=None, tracking=True)
    assert plan["source_fps"] is None
    assert plan["target_processing_fps"] == runtime.TRACKING_MIN_PROCESSING_FPS
    assert plan["source_limited"] is False
    assert any("unknown" in line for line in plan["rationale"])


def test_processing_and_submission_are_separate_concepts():
    plan = runtime.rate_plan(source_fps=30.0, tracking=True)
    assert plan["target_submission_hz"] < plan["target_processing_fps"], \
        "submitting every decoded frame is not the recommendation"
    assert plan["target_submission_hz"] == runtime.DEFAULT_TRACKING_SUBMISSION_HZ
    assert set(runtime.RATE_DEFINITIONS) == {"source_fps", "processing_fps", "submission_hz"}
    assert any("separate rate" in line for line in plan["rationale"])


def test_submission_never_exceeds_processing():
    """A 5 FPS source must not be told to submit at 5 Hz *and* process at 5."""
    plan = runtime.rate_plan(source_fps=3.0, tracking=True)
    assert plan["target_submission_hz"] <= plan["target_processing_fps"] == 3.0


def test_a_tight_fusion_tolerance_raises_submission_but_not_above_processing():
    plan = runtime.rate_plan(source_fps=30.0, tracking=True, multiview_time_tolerance_s=0.1)
    assert plan["target_submission_hz"] == 10.0
    assert plan["target_submission_hz"] <= plan["target_processing_fps"]
    assert any("fusion tick" in line for line in plan["rationale"])


def test_an_untracked_workload_has_no_frame_rate_floor():
    """A queue-length measurement worker at 1 Hz is correct, not a defect."""
    plan = runtime.rate_plan(source_fps=30.0, tracking=False)
    assert plan["tracking_enabled"] is False
    assert plan["minimum_processing_fps"] is None
    assert plan["target_processing_fps"] == plan["target_submission_hz"] == 1.0


@pytest.mark.parametrize("value", [0, -1, None, "", "abc", float("nan"), 90000, 1e9])
def test_an_implausible_source_rate_becomes_an_honest_unknown(value):
    """cv2.CAP_PROP_FPS returns 0 or a container timebase more often than not."""
    assert runtime.clean_fps(value) is None
    assert runtime.rate_plan(source_fps=value, tracking=True)["source_fps"] is None


# ---------------------------------------------------------------------------
# scoring what a worker achieved
# ---------------------------------------------------------------------------

def test_a_worker_at_target_is_ok():
    plan = runtime.rate_plan(source_fps=30.0)
    assert runtime.assess(plan, processing_fps=29.0)["state"] == "ok"


def test_a_slow_worker_is_flagged_with_causes_not_called_healthy():
    plan = runtime.rate_plan(source_fps=30.0)
    verdict = runtime.assess(plan, processing_fps=4.0, device="cpu")
    assert verdict["state"] == "below_target"
    assert "4 FPS against a 30 FPS target" in verdict["reason"]
    assert verdict["likely_causes"], "a finding with no suggested cause is not actionable"
    assert any("CPU" in cause for cause in verdict["likely_causes"])


def test_an_unreported_rate_is_unknown_not_assumed_fine():
    plan = runtime.rate_plan(source_fps=30.0)
    verdict = runtime.assess(plan, processing_fps=None)
    assert verdict["state"] == "unreported"
    assert "arriving samples alone do not prove it" in verdict["reason"]


def test_a_source_limited_worker_is_not_blamed_for_the_camera():
    plan = runtime.rate_plan(source_fps=6.0)
    verdict = runtime.assess(plan, processing_fps=6.0)
    assert verdict["state"] == "source_limited"
    assert "not a worker fault" in verdict["reason"]


# ---------------------------------------------------------------------------
# acceleration guidance
# ---------------------------------------------------------------------------

def test_cuda_is_recommended_but_never_required():
    plan = runtime.acceleration_plan()
    assert plan["device_preference"][0] == "cuda"
    assert plan["cpu_fallback"]["supported"] is True
    assert "not a requirement" in plan["cpu_fallback"]["rule"]
    assert "never makes a camera unusable" in plan["not_a_prerequisite"]
    assert "Never enable FP16 on a CPU path" in plan["fp16"]
    assert "another ML framework" in plan["framework"]
    # The platform describes checks; it never claims to have run them.
    assert plan["decided_by"].endswith("never ManySight")
    assert "nvidia-smi" in plan["probe"]["shell"]
    assert any("torch.cuda.is_available" in check for check in plan["probe"]["python"])


def test_the_three_readiness_questions_stay_separate():
    axes = runtime.readiness_axes()
    assert set(axes) == {"camera_available", "perception_runnable", "performance_capable", "note"}
    assert "Independent of any GPU" in axes["camera_available"]
    assert "CPU is a valid answer" in axes["perception_runnable"]
    assert "unusable because CUDA is unavailable" in axes["note"]


def test_environment_guidance_reuses_before_creating():
    plan = runtime.environment_plan()
    assert plan["reuse_first"] is True
    assert "existing project virtualenv or conda environment" in plan["order"][0]
    assert any("base interpreter" in item for item in plan["never"])
    assert any("ask the user" in item for item in plan["never"])


# ---------------------------------------------------------------------------
# the recipe endpoint
# ---------------------------------------------------------------------------

def test_the_recipe_recommends_the_tracking_floor(client):
    body = client.get("/api/v1/agent/worker-recipe").json()
    sampling = body["sampling"]
    assert sampling["tracking_minimum_processing_fps"] == 15.0
    assert sampling["preferred_processing_fps"] == 30.0
    assert sampling["recommendation"]["target_processing_fps"] >= 15.0
    assert set(sampling["rates"]) == {"source_fps", "processing_fps", "submission_hz"}
    assert set(sampling["report_in_heartbeat"]) == {
        "source_fps", "processing_fps", "submission_hz", "device", "precision"}
    assert any("1-5 FPS" in line for line in sampling["guidance"])
    assert any("hard-code a sleep" in line for line in sampling["guidance"])


def test_the_recipe_uses_a_measured_source_rate_when_given_one(client):
    fast = client.get("/api/v1/agent/worker-recipe", params={"source_fps": 60}).json()
    assert fast["sampling"]["recommendation"]["target_processing_fps"] == 30.0
    assert fast["sampling"]["recommendation"]["may_increase_to_fps"] == 60.0

    slow = client.get("/api/v1/agent/worker-recipe", params={"source_fps": 8}).json()
    assert slow["sampling"]["recommendation"]["target_processing_fps"] == 8.0
    assert slow["sampling"]["recommendation"]["source_limited"] is True


def test_the_recipe_plans_per_source_from_what_the_worker_reported(client):
    source_id = make_source(client, "Camera 3")
    job = client.post("/api/v1/jobs", json={
        "name": "Person tracking", "source_ids": [source_id]}).json()
    worker = client.post("/api/v1/workers", json={"job_id": job["id"], "name": "yolo"}).json()
    client.post(f"/api/v1/workers/{worker['id']}/heartbeat", json={
        "status": "running", "metrics": {"source_fps": 12.0, "processing_fps": 12.0}})

    body = client.get("/api/v1/agent/worker-recipe",
                      params={"source_ids": str(source_id)}).json()
    plan = body["sampling"]["per_source"][0]
    assert plan["source_id"] == source_id
    assert plan["source_fps_origin"] == "worker_heartbeat"
    assert plan["target_processing_fps"] == 12.0
    assert plan["source_limited"] is True
    # With one source named there is a single right answer, so it leads.
    assert body["sampling"]["recommendation"]["target_processing_fps"] == 12.0
    assert client.get("/api/v1/agent/worker-recipe",
                      params={"source_ids": "9999"}).status_code == 404


def test_the_recipe_reads_a_source_rate_recorded_on_the_source(client):
    source_id = client.post("/api/v1/sources", json={
        "name": "Camera 9", "kind": "webcam", "metadata": {"source_fps": 25}}).json()["id"]
    plan = client.get("/api/v1/agent/worker-recipe",
                      params={"source_ids": str(source_id)}).json()["sampling"]["per_source"][0]
    assert plan["source_fps"] == 25.0
    assert plan["source_fps_origin"] == "source_metadata"
    assert plan["target_processing_fps"] == 25.0


def test_an_unknown_source_rate_is_not_invented(client):
    source_id = make_source(client, "Camera 3")
    plan = client.get("/api/v1/agent/worker-recipe",
                      params={"source_ids": str(source_id)}).json()["sampling"]["per_source"][0]
    assert plan["source_fps"] is None
    assert plan["source_fps_origin"] == "unknown"


def test_the_recipe_tells_the_agent_to_probe_its_own_machine(client):
    body = client.get("/api/v1/agent/worker-recipe").json()
    assert body["acceleration"]["device_preference"] == ["cuda", "cpu"]
    assert body["acceleration"]["cpu_fallback"]["supported"] is True
    assert "probe_perception_runtime" in body["acceleration"]["probe"]["sdk"]
    assert body["local_environment"]["reuse_first"] is True
    assert body["readiness_axes"]["perception_runnable"]


def test_the_recipe_points_at_the_current_sdk_contract(client):
    """The agent must be sent to the SDK and DetectionSample, not to a stale script."""
    from server.routers.observations import DetectionSampleIn
    body = client.get("/api/v1/agent/worker-recipe").json()
    assert body["lifecycle"]["sdk"]["module"] == "sdk/python/manysight.py"
    assert "begin_detection_sample" in body["lifecycle"]["sdk"]["sample_builder"]
    assert body["submission"]["preferred_endpoint"] == "POST /api/v1/detection-samples"
    assert body["submission"]["envelope_fields"] == sorted(DetectionSampleIn.model_fields)
    assert "Do NOT infer it from an example" in body["authority"]
    assert "GET /api/v1/sources/{id}/connection" in body["source_access"]["resolve"]
    assert body["skill"] == "perception-workers"


# ---------------------------------------------------------------------------
# the perception endpoint
# ---------------------------------------------------------------------------

def _tracking_worker(client, source_id, metrics):
    job = client.post("/api/v1/jobs", json={
        "name": "Person tracking", "source_ids": [source_id]}).json()
    worker = client.post("/api/v1/workers", json={"job_id": job["id"], "name": "yolo"}).json()
    client.post(f"/api/v1/workers/{worker['id']}/heartbeat",
                json={"status": "running", "metrics": metrics})
    submit_sample(client, source_id, "s1", db.now(), [("a", 3.0, 2.0)])
    return worker


def test_a_slow_tracker_is_healthy_perception_and_a_performance_warning(client):
    source_id = make_source(client, "Camera 3")
    _tracking_worker(client, source_id, {"source_fps": 30.0, "processing_fps": 4.0,
                                         "device": "cpu"})
    body = client.get("/api/v1/agent/perception").json()

    # Availability is untouched: the observations are real.
    assert body["capability"]["state"] == "healthy"
    assert body["capability"]["action"] == "reuse"
    # But it is not silently called fine.
    assert body["performance"]["state"] == "below_target"
    assert body["performance"]["below_target_source_ids"] == [source_id]
    item = body["sources"][0]
    assert item["processing_fps"] == 4.0 and item["source_fps"] == 30.0
    assert item["device"] == "cpu"
    assert item["performance"]["likely_causes"]
    assert any("below their target processing rate" in reason for reason in body["reasons"])
    assert "below target" in body["next"]


def test_a_worker_at_target_raises_no_performance_warning(client):
    source_id = make_source(client, "Camera 3")
    _tracking_worker(client, source_id, {"source_fps": 30.0, "processing_fps": 30.0,
                                         "device": "cuda"})
    body = client.get("/api/v1/agent/perception").json()
    assert body["performance"]["state"] == "ok"
    assert body["performance"]["below_target_source_ids"] == []
    assert body["sources"][0]["performance"]["state"] == "ok"


def test_a_worker_that_reports_no_rate_is_unverified_not_verified(client):
    source_id = make_source(client, "Camera 3")
    _tracking_worker(client, source_id, {})
    body = client.get("/api/v1/agent/perception").json()
    assert body["performance"]["state"] == "unverified"
    assert body["performance"]["unreported_source_ids"] == [source_id]
    assert any("unverified" in reason for reason in body["reasons"])


def test_perception_never_reports_a_camera_unusable_for_want_of_a_gpu(client):
    """CPU-only is a performance fact, never an availability or connection fact."""
    source_id = client.post("/api/v1/sources", json={
        "name": "Camera 3", "kind": "http", "connection_management": "manysight_managed",
        "connection": {"url": "http://cam.internal/stream.mjpg"}}).json()["id"]
    _tracking_worker(client, source_id, {"source_fps": 30.0, "processing_fps": 30.0,
                                         "device": "cpu"})
    body = client.get("/api/v1/agent/perception").json()
    assert body["capability"]["state"] == "healthy"
    assert body["sources"][0]["available"] is True
    assert body["performance"]["state"] == "ok", "CPU at target rate is simply fine"
    assert "never makes a camera unusable" in body["readiness_axes"]["note"] \
        or "unusable because CUDA is unavailable" in body["readiness_axes"]["note"]

    source = client.get(f"/api/v1/agent/sources/{source_id}").json()
    assert source["connection"]["configured"] is True
    assert "cuda" not in str(source["connection"]).lower()


def test_perception_reports_a_measured_source_rate_the_caller_supplies(client):
    source_id = make_source(client, "Camera 3")
    _tracking_worker(client, source_id, {"processing_fps": 8.0})
    body = client.get("/api/v1/agent/perception", params={"source_fps": 10}).json()
    item = body["sources"][0]
    assert item["source_fps"] == 10.0 and item["source_fps_origin"] == "requested"
    assert item["rate_plan"]["source_limited"] is True
    assert body["performance"]["state"] == "source_limited"


def test_inspect_source_separates_the_three_rates(client):
    source_id = make_source(client, "Camera 3")
    _tracking_worker(client, source_id, {"source_fps": 30.0, "processing_fps": 28.0,
                                         "device": "cuda"})
    perception = client.get(f"/api/v1/agent/sources/{source_id}").json()["perception"]
    assert perception["source_fps"] == 30.0
    assert perception["processing_fps"] == 28.0
    assert perception["submission_hz"] is None or perception["submission_hz"] >= 0
    assert perception["device"] == "cuda"


def test_an_older_worker_reporting_local_fps_is_still_understood(client):
    """A worker started before the canonical key existed is still running."""
    source_id = make_source(client, "Camera 3")
    _tracking_worker(client, source_id, {"local_fps": 22.0})
    item = client.get("/api/v1/agent/perception").json()["sources"][0]
    assert item["processing_fps"] == 22.0


# ---------------------------------------------------------------------------
# naming
# ---------------------------------------------------------------------------

def guidance_surfaces(client) -> dict[str, str]:
    """Every place this milestone's worker guidance is written down."""
    return {
        "worker-recipe": json.dumps(client.get("/api/v1/agent/worker-recipe").json()),
        "perception": json.dumps(client.get("/api/v1/agent/perception").json()),
        "frame-capture-plan": json.dumps(
            client.get(f"/api/v1/agent/sources/{make_source(client, 'Camera 7')}"
                       "/frame-capture-plan").json()),
        "run-person-tracking": json.dumps(
            client.get("/api/v1/agent/workflows/run-person-tracking").json()),
        "perception-workers": open("skills/perception-workers/SKILL.md", encoding="utf-8").read(),
        "sdk": open("sdk/python/manysight.py", encoding="utf-8").read(),
        "worker_runtime": open("server/services/worker_runtime.py", encoding="utf-8").read(),
    }


def test_the_worker_guidance_never_names_the_retired_project(client):
    from test_branding_audit import LEGACY

    for name, text in guidance_surfaces(client).items():
        assert not LEGACY.search(text), f"{name} still names the previous project"


def test_the_worker_guidance_asks_for_no_credential_access_key(client):
    """Guidance is where a removed prerequisite lingers longest.

    An agent told to set a variable the server no longer reads will conclude the
    setup is incomplete and stop, so the removal has to reach the prose as well
    as the code.
    """
    # Assembled, so this audit is not itself a match for what it forbids.
    access_key = re.compile(r"\b[A-Z][A-Z0-9_]*CREDENTIAL_" + r"ACCESS_KEY\b")
    dedicated_header = re.compile(r"\bX-[A-Za-z]+-Credential-Key\b")

    surfaces = guidance_surfaces(client)
    for name, text in surfaces.items():
        assert not access_key.search(text), f"{name} still asks for a credential access key"
        assert not dedicated_header.search(text), f"{name} still sends a dedicated header"
    # Not vacuous: the guidance really does cover resolving a managed connection.
    assert "/connection" in surfaces["worker-recipe"]
    assert "sources/{id}/connection" in surfaces["worker-recipe"]


# ---------------------------------------------------------------------------
# the SDK's local probe — no real GPU required
# ---------------------------------------------------------------------------

def test_the_probe_recommends_cuda_and_fp16_on_a_modern_device(monkeypatch):
    manysight = sdk()
    monkeypatch.setattr(manysight, "_probe_torch", lambda: {
        "installed": True, "version": "2.5.0", "cuda_build": "12.4", "cuda_available": True,
        "device_count": 1, "device_name": "NVIDIA RTX 4090", "compute_capability": [8, 9],
        "error": None})
    monkeypatch.setattr(manysight, "_probe_nvidia_smi", lambda: {
        "present": True, "driver_version": "550.54", "devices": ["NVIDIA RTX 4090"],
        "error": None})

    probe = manysight.probe_perception_runtime()
    assert probe["recommended_device"] == "cuda"
    assert probe["fp16_supported"] is True
    assert probe["torch"]["device_name"] == "NVIDIA RTX 4090"


def test_the_probe_keeps_fp32_on_an_older_cuda_device(monkeypatch):
    manysight = sdk()
    monkeypatch.setattr(manysight, "_probe_torch", lambda: {
        "installed": True, "version": "2.5.0", "cuda_build": "11.8", "cuda_available": True,
        "device_count": 1, "device_name": "NVIDIA GTX 1080", "compute_capability": [6, 1],
        "error": None})
    monkeypatch.setattr(manysight, "_probe_nvidia_smi", lambda: {
        "present": True, "driver_version": "535.0", "devices": ["NVIDIA GTX 1080"],
        "error": None})

    probe = manysight.probe_perception_runtime()
    assert probe["recommended_device"] == "cuda"
    assert probe["fp16_supported"] is False, "FP16 must not be enabled blindly"
    assert any("keep FP32" in note for note in probe["notes"])


def test_the_probe_falls_back_to_cpu_without_failing(monkeypatch):
    manysight = sdk()
    monkeypatch.setattr(manysight, "_probe_torch", lambda: {
        "installed": False, "version": None, "cuda_build": None, "cuda_available": False,
        "device_count": 0, "device_name": None, "compute_capability": None,
        "error": "ModuleNotFoundError: torch"})
    monkeypatch.setattr(manysight, "_probe_nvidia_smi", lambda: {
        "present": False, "driver_version": None, "devices": [], "error": "not on PATH"})

    probe = manysight.probe_perception_runtime()
    assert probe["recommended_device"] == "cpu"
    assert probe["fp16_supported"] is False
    assert probe["notes"], "a CPU-only machine gets an explanation, not silence"


def test_the_probe_names_the_wrong_environment_when_a_gpu_is_present_but_unusable(monkeypatch):
    manysight = sdk()
    monkeypatch.setattr(manysight, "_probe_torch", lambda: {
        "installed": True, "version": "2.5.0+cpu", "cuda_build": None, "cuda_available": False,
        "device_count": 0, "device_name": None, "compute_capability": None, "error": None})
    monkeypatch.setattr(manysight, "_probe_nvidia_smi", lambda: {
        "present": True, "driver_version": "550.54", "devices": ["NVIDIA RTX 4090"],
        "error": None})

    probe = manysight.probe_perception_runtime()
    assert probe["recommended_device"] == "cpu"
    assert any("cannot use it" in note for note in probe["notes"])


def test_the_probe_runs_for_real_on_this_machine_whatever_it_is():
    """Smoke: it must never raise, on a GPU box or in CI."""
    probe = sdk().probe_perception_runtime()
    assert probe["recommended_device"] in {"cuda", "cpu"}
    assert probe["environment"]["kind"] in {"conda", "venv", "system"}
    assert probe["python_executable"] == sys.executable
    if probe["recommended_device"] == "cuda":          # only when real hardware is present
        assert probe["torch"]["device_name"]


# ---------------------------------------------------------------------------
# SDK rate helpers
# ---------------------------------------------------------------------------

class FakeCapture:
    def __init__(self, declared):
        self.declared = declared

    def get(self, prop):
        return self.declared


def test_capture_fps_rejects_a_meaningless_property_value():
    manysight = sdk()
    assert manysight.capture_fps(FakeCapture(30.0)) == 30.0
    assert manysight.capture_fps(FakeCapture(0.0)) is None
    assert manysight.capture_fps(FakeCapture(90000.0)) is None


def test_capture_fps_can_measure_when_the_property_lies():
    manysight = sdk()

    class Timed(FakeCapture):
        def read(self):
            return True, object()

    measured = manysight.capture_fps(Timed(0.0), measure_frames=5)
    assert measured is None or measured > 0


def test_the_submission_gate_paces_submission_without_slowing_the_loop():
    """The gate answers 'submit now?'; it never sleeps, so the tracker runs free."""
    gate = sdk().SubmissionGate(5.0)          # 200 ms
    assert gate.due(now=100.0) is True
    assert gate.due(now=100.1) is False       # frame still processed, just not submitted
    assert gate.due(now=100.25) is True
    # A pause must not produce a catch-up burst.
    assert gate.due(now=110.0) is True
    assert gate.due(now=110.05) is False
    with pytest.raises(ValueError):
        sdk().SubmissionGate(0)
