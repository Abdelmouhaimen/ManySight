"""The live coordinator: latest-wins, dirty-only, no backlog, durable history.

The property under test throughout is the split the refactor is built on —
*live state favours freshness, raw history favours completeness*. Nothing here
may pass by making history lossy, and nothing may pass by making live state
replay obsolete frames.
"""
import asyncio
import contextlib
import threading
import time

import pytest

from helpers import sync_live_state

from server import db
from server.services import current_state, multiview, realtime
from server.services.metrics import Registry, percentiles


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


@pytest.fixture(autouse=True)
def deterministic_scheduler(monkeypatch):
    """Drive ticks explicitly instead of on the 100 Hz clock.

    The background scheduler is real and has its own tests below. Leaving it
    running for the behavioural tests would make "how many ticks happened"
    depend on how long the test took. Everything else stays real: reads still
    drain, so the read-your-writes assertions exercise the production path.
    """
    monkeypatch.setattr(realtime, "ENABLED", False)


@pytest.fixture
def scene(client, calibrated_source):
    """Two calibrated cameras, one zone, one group — and a clean coordinator."""
    realtime.coordinator.reset()
    second = client.post("/api/v1/sources", json={"name": "Camera 2", "kind": "http"}).json()["id"]
    calibrate(client, second)
    zone = client.post("/api/v1/zones", json={
        "name": "Aisle", "ztype": "aisle",
        "polygon": [{"x": 0, "y": 0}, {"x": 10, "y": 0}, {"x": 10, "y": 10}, {"x": 0, "y": 10}],
    }).json()
    group = client.post("/api/v1/multiview/groups", json={
        "name": "Pair", "source_ids": [calibrated_source, second],
        "time_tolerance_s": 0.5, "spatial_gate_m": 0.8, "track_age_s": 30,
    }).json()
    yield {"client": client, "sources": [calibrated_source, second],
           "zone": zone, "group": group}
    realtime.coordinator.reset()


def post_sample(scene, source_index, sample_id, ts, tracks):
    response = scene["client"].post("/api/v1/detection-samples", json={
        "schema_version": 2, "source_id": scene["sources"][source_index],
        "sample_id": sample_id, "timestamp": ts, "entity_type": "person",
        "detections": [{"entity_id": entity_id, "point_px": [x * 100, y * 100],
                        "confidence": 0.9, "identity_scope": "source"}
                       for entity_id, x, y in tracks],
    })
    assert response.status_code == 200, response.text
    return response.json()


def fused(scene):
    return scene["client"].get("/api/v1/multiview/current", params={
        "group_id": scene["group"]["id"]}).json()


def _stable(entities):
    """Fused entities without the fields that move with the wall clock."""
    return [{key: value for key, value in entity.items() if key != "freshness_s"}
            for entity in entities]


def stored_samples(source_id):
    return db.q(
        "SELECT sample_id, value expected, ts FROM events WHERE source_id=? "
        "AND event_type='measurement' AND name=? ORDER BY id",
        (source_id, current_state.FRAME_COUNT_NAME))


# --------------------------------------------------------------------------
# Raw durability vs live coalescing
# --------------------------------------------------------------------------

def test_superseded_frames_stay_durable_and_only_the_newest_is_fused(scene):
    """Four frames arrive between ticks: four rows, one fusion."""
    base = db.now()
    for frame in range(4):
        post_sample(scene, 0, f"c1-f{frame}", base + frame * 0.01,
                    [("a", 2.0 + frame * 0.01, 2.0)])
    stored = stored_samples(scene["sources"][0])
    assert [row["sample_id"] for row in stored] == [f"c1-f{n}" for n in range(4)]
    assert db.q1("SELECT COUNT(*) n FROM events WHERE event_type='detection'")["n"] == 4

    # Current source state is the newest frame only — that is the read model,
    # not a claim about history.
    current = db.q1("SELECT sample_id FROM source_current_samples WHERE source_id=?",
                    (scene["sources"][0],))
    assert current["sample_id"] == "c1-f3"

    sync_live_state()
    members = db.q("SELECT DISTINCT sample_key FROM fused_entity_members")
    assert [row["sample_key"] for row in members] == ["id:c1-f3"]


def test_coalescing_is_counted_as_coalesced_not_dropped(scene):
    from server.services.metrics import registry
    registry.reset()
    base = db.now()
    for frame in range(5):
        post_sample(scene, 0, f"count-{frame}", base + frame * 0.01, [("a", 2.0, 2.0)])
    counters = registry.snapshot()["counters"]
    assert counters["realtime.source_updates"] == 5
    # Four superseded live updates; five durable samples.
    assert counters["realtime.live_updates_coalesced"] == 4
    assert len(stored_samples(scene["sources"][0])) == 5


def test_a_read_never_sees_state_older_than_the_newest_committed_sample(scene):
    """Reads drain the scheduler, so the API is read-your-writes."""
    base = db.now()
    post_sample(scene, 0, "r1", base, [("a", 2.0, 2.0)])
    post_sample(scene, 1, "r2", base, [("x", 2.05, 2.0)])
    current = fused(scene)
    assert len(current["entities"]) == 1
    assert len(current["entities"][0]["members"]) == 2
    assert current["groups"][0]["quality"] == "known"


# --------------------------------------------------------------------------
# Dirty-only execution and race safety
# --------------------------------------------------------------------------

def test_a_clean_group_is_not_refused_but_is_not_re_fused(scene):
    base = db.now()
    post_sample(scene, 0, "d1", base, [("a", 2.0, 2.0)])
    assert realtime.coordinator.pending_groups()
    assert realtime.coordinator.drain() == 1
    assert realtime.coordinator.pending_groups() == []
    # Nothing new arrived: the scheduler has no work at all.
    assert realtime.coordinator.drain() == 0


def test_only_sources_that_changed_are_re_associated(scene):
    from server.services.metrics import registry
    base = db.now()
    post_sample(scene, 0, "s1", base, [("a", 2.0, 2.0)])
    post_sample(scene, 1, "s2", base, [("x", 2.05, 2.0)])
    sync_live_state()

    registry.reset()
    post_sample(scene, 1, "s3", base + 0.1, [("x", 2.06, 2.0)])
    sync_live_state()
    # Camera 1 did not change, so its sample is not staged again — but its
    # current entity still contributes to the refreshed fused position.
    assert registry.counter("fusion.source_stages") == 1
    assert len(fused(scene)["entities"][0]["members"]) == 2


def test_a_sample_arriving_during_a_tick_leaves_the_group_dirty(scene):
    """Race safety: clearing dirty must be conditional on the sequence."""
    base = db.now()
    post_sample(scene, 0, "race-1", base, [("a", 2.0, 2.0)])
    group_key = realtime.coordinator.pending_groups()[0]

    original = multiview.run_group_tick
    arrived = threading.Event()

    def tick_then_publish(group, entity_type, samples, as_of):
        original(group, entity_type, samples, as_of)
        if not arrived.is_set():
            arrived.set()
            # Simulate a frame accepted while this tick was still running.
            realtime.coordinator.publish([{
                "source_id": scene["sources"][0], "entity_type": "person",
                "sample_id": "race-2", "sample_key": "id:race-2",
                "timestamp": base + 0.05}])

    multiview.run_group_tick = tick_then_publish
    try:
        realtime.coordinator._run_group_tick(group_key)
    finally:
        multiview.run_group_tick = original
    assert arrived.is_set()
    assert group_key in realtime.coordinator.pending_groups(), (
        "a sample accepted during the tick must not be marked clean by it")


def test_a_failing_tick_keeps_the_group_dirty_and_does_not_raise(scene):
    base = db.now()
    post_sample(scene, 0, "fail-1", base, [("a", 2.0, 2.0)])
    group_key = realtime.coordinator.pending_groups()[0]

    original = multiview.run_group_tick
    multiview.run_group_tick = lambda *args: (_ for _ in ()).throw(RuntimeError("boom"))
    try:
        assert realtime.coordinator._run_group_tick(group_key) is False
    finally:
        multiview.run_group_tick = original
    assert group_key in realtime.coordinator.pending_groups()
    assert realtime.coordinator.drain() == 1


# --------------------------------------------------------------------------
# Scenario coverage
# --------------------------------------------------------------------------

def test_an_empty_complete_frame_updates_live_state(scene):
    """detections=[] is a known zero, not a missing frame."""
    base = db.now()
    post_sample(scene, 0, "e1", base, [("a", 2.0, 2.0)])
    post_sample(scene, 1, "e2", base, [("x", 2.05, 2.0)])
    assert len(fused(scene)["entities"]) == 1

    from server.services.metrics import registry
    registry.reset()
    post_sample(scene, 0, "e3", base + 0.1, [])
    post_sample(scene, 1, "e4", base + 0.1, [])
    assert registry.counter("realtime.source_updates") == 2
    current = fused(scene)
    assert current["entities"] == []
    assert current["groups"][0]["quality"] == "known"
    occupancy = scene["client"].get("/api/v1/multiview/occupancy", params={
        "group_id": scene["group"]["id"], "zone_id": scene["zone"]["id"]}).json()
    assert occupancy["value"] == 0
    assert occupancy["quality"] == "known"


def test_asymmetric_camera_rates_need_no_alignment(scene):
    """One camera at 4x the rate of the other still fuses on freshest state."""
    base = db.now()
    post_sample(scene, 1, "slow-0", base, [("x", 2.05, 2.0)])
    for frame in range(4):
        post_sample(scene, 0, f"fast-{frame}", base + frame * 0.02, [("a", 2.0, 2.0)])
        sync_live_state()
    current = fused(scene)
    assert len(current["entities"]) == 1
    assert len(current["entities"][0]["members"]) == 2
    assert len(stored_samples(scene["sources"][0])) == 4
    assert len(stored_samples(scene["sources"][1])) == 1


def test_a_stopped_camera_degrades_quality_without_blocking_the_others(scene):
    base = db.now()
    post_sample(scene, 0, "both-1", base, [("a", 2.0, 2.0)])
    post_sample(scene, 1, "both-2", base, [("x", 2.05, 2.0)])
    assert fused(scene)["groups"][0]["quality"] == "known"

    # Camera 2 stops. Its last frame must not stay fresh forever.
    db.ex("UPDATE source_current_samples SET ts=ts-3600 WHERE source_id=?",
          (scene["sources"][1],))
    db.ex("UPDATE source_current_entities SET ts=ts-3600 WHERE source_id=?",
          (scene["sources"][1],))
    post_sample(scene, 0, "alone-1", base + 0.1, [("a", 2.1, 2.0)])
    current = fused(scene)
    assert current["groups"][0]["quality"] == "partial"
    assert current["groups"][0]["fresh_source_ids"] == [scene["sources"][0]]
    assert [member["source_id"] for member in current["entities"][0]["members"]] \
        == [scene["sources"][0]]
    assert realtime.coordinator.pending_groups() == [], "no backlog waits on the stopped camera"


def test_an_out_of_order_frame_does_not_move_live_state_backwards(scene):
    base = db.now()
    post_sample(scene, 0, "newer", base + 1.0, [("a", 5.0, 5.0)])
    post_sample(scene, 0, "older", base, [("a", 1.0, 1.0)])

    # Both are durable evidence.
    assert {row["sample_id"] for row in stored_samples(scene["sources"][0])} == {"newer", "older"}
    # Current source state stays on the newer frame.
    current = db.q1("SELECT sample_id, ts FROM source_current_samples WHERE source_id=?",
                    (scene["sources"][0],))
    assert current["sample_id"] == "newer"
    sync_live_state()
    entity = fused(scene)["entities"][0]
    assert entity["point_map"]["x"] == pytest.approx(5.0)


def test_a_duplicate_sample_does_not_re_fuse_or_duplicate_evidence(scene):
    from server.services.metrics import registry
    base = db.now()
    body = {"schema_version": 2, "source_id": scene["sources"][0], "sample_id": "dup",
            "timestamp": base, "entity_type": "person",
            "detections": [{"entity_id": "a", "point_px": [200, 200],
                            "confidence": 0.9, "identity_scope": "source"}]}
    first = scene["client"].post("/api/v1/detection-samples", json=body).json()
    assert first["sample_status"] == "completed"
    sync_live_state()

    registry.reset()
    repeat = scene["client"].post("/api/v1/detection-samples", json=body).json()
    assert repeat["sample_status"] == "duplicate"
    assert repeat["accepted"] == 0
    assert registry.counter("realtime.source_updates") == 0
    assert realtime.coordinator.pending_groups() == []
    assert len(stored_samples(scene["sources"][0])) == 1
    assert db.q1("SELECT COUNT(*) n FROM events WHERE event_type='detection'")["n"] == 1


def test_fusion_is_deterministic_over_identical_evidence(client, calibrated_source):
    """Same evidence twice, in two workspaces, must decide identically."""
    def run():
        realtime.coordinator.reset()
        second = client.post("/api/v1/sources",
                             json={"name": "B", "kind": "http"}).json()["id"]
        calibrate(client, second)
        client.post("/api/v1/zones", json={
            "name": f"Zone {second}", "ztype": "area",
            "polygon": [{"x": 0, "y": 0}, {"x": 10, "y": 0},
                        {"x": 10, "y": 10}, {"x": 0, "y": 10}]})
        group = client.post("/api/v1/multiview/groups", json={
            "name": f"G{second}", "source_ids": [calibrated_source, second],
            "time_tolerance_s": 0.5, "spatial_gate_m": 0.8, "track_age_s": 30}).json()
        # Real wall-clock time: fused state has a freshness window, and a
        # 1970 timestamp would be aged out before it could be read back.
        base = db.now()
        scene = {"client": client, "sources": [calibrated_source, second],
                 "group": group}
        for step in range(6):
            post_sample(scene, 0, f"{second}-a{step}", base + step * 0.1,
                        [("a", 2 + step * 0.1, 5), ("b", 8 - step * 0.1, 5)])
            post_sample(scene, 1, f"{second}-b{step}", base + step * 0.1,
                        [("x", 2.05 + step * 0.1, 5), ("y", 7.95 - step * 0.1, 5)])
            sync_live_state()
        # Sorted, because the API orders by the opaque fused id and the two runs
        # mint different ones. What must be identical is the decision: which
        # local tracks became one entity, where it is, and which zone it is in.
        return sorted(
            (round(entity["point_map"]["x"], 6), round(entity["point_map"]["y"], 6),
             entity["zone_id"],
             tuple(sorted((member["source_id"] == calibrated_source,
                           member["local_entity_id"])
                          for member in entity["members"])))
            for entity in fused(scene)["entities"])

    first, second_run = run(), run()
    assert first == second_run
    assert len(first) == 2
    assert all(len(entity[3]) == 2 for entity in first)


# --------------------------------------------------------------------------
# Scheduler mechanics
# --------------------------------------------------------------------------

def test_missed_deadlines_are_dropped_not_queued():
    """A slow tick must not leave a backlog of scheduled ticks behind it."""
    from server.services.metrics import registry
    coordinator = realtime.RealtimeStateCoordinator(interval_s=0.01)
    registry.reset()
    ticks = []

    def slow_tick():
        ticks.append(time.monotonic())
        time.sleep(0.05)  # five deadlines' worth of work

    coordinator._run_due_ticks = slow_tick
    coordinator._dirty[("x", 1, "person")] = 1

    async def run():
        coordinator._running = True
        task = asyncio.create_task(coordinator._scheduler())
        await asyncio.sleep(0.4)
        coordinator._running = False
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(run())
    # ~0.4 s at 100 Hz is 40 deadlines; 50 ms of work each means about 8 ticks.
    # A backlog implementation would have executed all 40.
    assert 4 <= len(ticks) <= 14, len(ticks)
    assert registry.counter("realtime.deadlines_missed") > 0
    gaps = [later - earlier for earlier, later in zip(ticks, ticks[1:])]
    assert all(gap >= 0.045 for gap in gaps), gaps


def test_the_scheduler_skips_clean_ticks():
    from server.services.metrics import registry
    coordinator = realtime.RealtimeStateCoordinator(interval_s=0.01)
    registry.reset()
    coordinator._run_due_ticks = lambda: pytest.fail("a clean group must not be fused")

    async def run():
        coordinator._running = True
        task = asyncio.create_task(coordinator._scheduler())
        await asyncio.sleep(0.15)
        coordinator._running = False
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert registry.counter("realtime.ticks_skipped_clean") >= 5


def test_the_coordinator_recovers_live_state_after_a_restart(scene):
    """A restart must not need a new camera frame to become coherent."""
    base = db.now()
    post_sample(scene, 0, "boot-1", base, [("a", 2.0, 2.0)])
    post_sample(scene, 1, "boot-2", base, [("x", 2.05, 2.0)])
    sync_live_state()
    before = _stable(fused(scene)["entities"])

    realtime.coordinator.reset()
    assert realtime.coordinator.pending_groups() == []
    assert realtime.coordinator.reconcile() > 0
    assert realtime.coordinator.pending_groups(), "restart marks groups for reconciliation"
    # Seeded snapshots are already-consumed: reconciliation refreshes, it does
    # not re-associate evidence that is already persisted.
    from server.services.metrics import registry
    registry.reset()
    realtime.coordinator.drain()
    assert registry.counter("fusion.source_stages") == 0
    assert _stable(fused(scene)["entities"]) == before


def test_the_execution_model_is_reported_and_warns_about_extra_workers(monkeypatch):
    monkeypatch.delenv("WEB_CONCURRENCY", raising=False)
    monkeypatch.delenv("UVICORN_WORKERS", raising=False)
    assert realtime.execution_model()["warning"] is None
    monkeypatch.setenv("WEB_CONCURRENCY", "4")
    model = realtime.execution_model()
    assert model["declared_worker_processes"] == 4
    assert "one process per workspace" in model["warning"]


def test_metrics_are_bounded_and_never_report_dropped_evidence(client):
    registry = Registry()
    for index in range(5000):
        registry.observe("x", index / 1000.0)
    snapshot = registry.snapshot()
    assert snapshot["durations_ms"]["x"]["count"] == 2048
    assert percentiles([])["count"] == 0
    assert client.get("/api/v1/realtime/metrics").json()["raw_evidence_dropped"] == 0
