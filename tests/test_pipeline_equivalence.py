"""The optimized hot paths must decide exactly what the paths they replaced did.

Three rewrites are covered here, each by comparing the new implementation with
the old one on the same data rather than by re-asserting the new behaviour:

* source-current materialization from the batch in memory vs. re-reading `events`
* batched membership / trajectory / member-evidence preloads vs. per-row queries
* the exact assignment solver, whose semantics gate any future replacement
"""
import math
import random

import pytest

from helpers import sync_live_state

from server import db
from server.services import current_state, multiview
from server.services.multiview import minimum_cost_assignment


def calibrate(client, source_id):
    assert client.put(f"/api/v1/sources/{source_id}/calibration", json={
        "points": [
            {"px": {"x": 0, "y": 0}, "map": {"x": 0, "y": 0}},
            {"px": {"x": 1000, "y": 0}, "map": {"x": 10, "y": 0}},
            {"px": {"x": 1000, "y": 1000}, "map": {"x": 10, "y": 10}},
            {"px": {"x": 0, "y": 1000}, "map": {"x": 0, "y": 10}},
        ], "frame_w": 1000, "frame_h": 1000,
    }).status_code == 200


def current_state_rows(source_id):
    return (
        db.q("SELECT * FROM source_current_samples WHERE source_id=? ORDER BY entity_type",
             (source_id,)),
        db.q("SELECT * FROM source_current_entities WHERE source_id=? "
             "ORDER BY sample_key, event_id", (source_id,)),
    )


# --------------------------------------------------------------------------
# Source-current materialization
# --------------------------------------------------------------------------

def test_in_memory_materialization_equals_rereading_the_rows(client, calibrated_source):
    """Same sample, both paths, identical read-model rows."""
    sample = {
        "schema_version": 2, "source_id": calibrated_source, "sample_id": "equiv-1",
        "timestamp": 1000.5, "frame_index": 7, "entity_type": "person",
        "detections": [
            {"entity_id": "a", "point_px": [200, 200], "confidence": 0.8,
             "identity_scope": "source"},
            {"entity_id": "b", "bbox_px": [400, 100, 500, 600], "confidence": 0.4},
            {"entity_id": "c", "point_px": [900, 900]},
        ],
    }
    assert client.post("/api/v1/detection-samples", json=sample).status_code == 200
    fast_samples, fast_entities = current_state_rows(calibrated_source)
    assert fast_samples and len(fast_entities) == 3

    # Force the re-reading path over exactly the same stored rows.
    db.ex("DELETE FROM source_current_samples WHERE source_id=?", (calibrated_source,))
    db.ex("DELETE FROM source_current_entities WHERE source_id=?", (calibrated_source,))
    assert current_state.materialize_sample(
        calibrated_source, "person", "equiv-1", 1000.5) is not None
    slow_samples, slow_entities = current_state_rows(calibrated_source)

    assert fast_samples == slow_samples
    assert fast_entities == slow_entities


def test_an_empty_frame_materializes_identically_on_both_paths(client, calibrated_source):
    assert client.post("/api/v1/detection-samples", json={
        "schema_version": 2, "source_id": calibrated_source, "sample_id": "empty-1",
        "timestamp": 2000.0, "entity_type": "person", "detections": [],
    }).status_code == 200
    fast = current_state_rows(calibrated_source)

    db.ex("DELETE FROM source_current_samples WHERE source_id=?", (calibrated_source,))
    db.ex("DELETE FROM source_current_entities WHERE source_id=?", (calibrated_source,))
    assert current_state.materialize_sample(
        calibrated_source, "person", "empty-1", 2000.0) is not None
    assert current_state_rows(calibrated_source) == fast


def test_a_sample_split_across_batches_still_uses_the_rereading_path(client, calibrated_source):
    """Detections first, marker second: only the re-read path can complete this."""
    detections = client.post("/api/v1/observations/batch", json={"observations": [
        {"schema_version": 2, "observation_id": "split-d1", "sample_id": "split",
         "kind": "detection", "timestamp": 3000.0, "source_id": calibrated_source,
         "entity_id": "a", "entity_type": "person", "geometry": {"point_px": [200, 200]}},
    ]})
    assert detections.status_code == 200
    assert detections.json()["completed_samples"] == 0
    assert db.q("SELECT * FROM source_current_samples") == []

    marker = client.post("/api/v1/observations/batch", json={"observations": [
        {"schema_version": 2, "observation_id": "split-m1", "sample_id": "split",
         "kind": "measurement", "timestamp": 3000.0, "source_id": calibrated_source,
         "name": current_state.FRAME_COUNT_NAME, "label": "person", "value": 1},
    ]})
    assert marker.status_code == 200
    assert marker.json()["completed_samples"] == 1
    samples, entities = current_state_rows(calibrated_source)
    assert samples[0]["sample_id"] == "split"
    assert len(entities) == 1


def test_an_incomplete_sample_never_replaces_current_state(client, calibrated_source):
    """Marker says two, one arrived: raw evidence yes, current state no."""
    response = client.post("/api/v1/observations/batch", json={"observations": [
        {"schema_version": 2, "observation_id": "short-d1", "sample_id": "short",
         "kind": "detection", "timestamp": 4000.0, "source_id": calibrated_source,
         "entity_id": "a", "entity_type": "person", "geometry": {"point_px": [200, 200]}},
        {"schema_version": 2, "observation_id": "short-m1", "sample_id": "short",
         "kind": "measurement", "timestamp": 4000.0, "source_id": calibrated_source,
         "name": current_state.FRAME_COUNT_NAME, "label": "person", "value": 2},
    ]})
    assert response.status_code == 200
    assert response.json()["completed_samples"] == 0
    assert response.json()["accepted"] == 2          # raw evidence is durable
    assert db.q("SELECT * FROM source_current_samples") == []


def test_rebuild_from_history_reproduces_the_live_read_model(client, calibrated_source):
    for frame in range(3):
        assert client.post("/api/v1/detection-samples", json={
            "schema_version": 2, "source_id": calibrated_source,
            "sample_id": f"rebuild-{frame}", "timestamp": 5000.0 + frame,
            "entity_type": "person",
            "detections": [{"entity_id": "a", "point_px": [200 + frame * 10, 200]}],
        }).status_code == 200
    live = current_state_rows(calibrated_source)

    db.ex("DELETE FROM source_current_samples")
    db.ex("DELETE FROM source_current_entities")
    assert current_state.rebuild_from_history() == 1
    assert current_state_rows(calibrated_source) == live


# --------------------------------------------------------------------------
# Batched fusion lookups
# --------------------------------------------------------------------------

@pytest.fixture
def fused_scene(client, calibrated_source):
    second = client.post("/api/v1/sources", json={"name": "B", "kind": "http"}).json()["id"]
    calibrate(client, second)
    client.post("/api/v1/zones", json={
        "name": "Zone", "ztype": "area",
        "polygon": [{"x": 0, "y": 0}, {"x": 10, "y": 0}, {"x": 10, "y": 10}, {"x": 0, "y": 10}]})
    group = client.post("/api/v1/multiview/groups", json={
        "name": "Pair", "source_ids": [calibrated_source, second],
        "time_tolerance_s": 0.5, "spatial_gate_m": 0.8, "track_age_s": 30}).json()
    base = db.now()
    for step in range(3):
        for index, source_id in enumerate((calibrated_source, second)):
            assert client.post("/api/v1/detection-samples", json={
                "schema_version": 2, "source_id": source_id,
                "sample_id": f"s{source_id}-{step}", "timestamp": base + step * 0.1,
                "entity_type": "person",
                "detections": [
                    {"entity_id": f"t{index}-1", "point_px": [200 + step * 10 + index * 5, 500],
                     "confidence": 0.9, "identity_scope": "source"},
                    {"entity_id": f"t{index}-2", "point_px": [800 - step * 10 - index * 5, 500],
                     "confidence": 0.9, "identity_scope": "source"},
                ]}).status_code == 200
        sync_live_state()
    return {"group": db.q1("SELECT * FROM multiview_groups WHERE id=?", (group["id"],)),
            "sources": [calibrated_source, second], "base": base}


def test_batched_membership_lookup_matches_the_per_row_query(fused_scene):
    group_id = fused_scene["group"]["id"]
    for source_id in fused_scene["sources"]:
        local = db.q("SELECT * FROM source_current_entities WHERE source_id=? "
                     "AND entity_type='person'", (source_id,))
        batched = multiview._load_existing_memberships(group_id, source_id, local)
        for row in local:
            per_row = db.q1(
                "SELECT m.fused_entity_id FROM fused_entity_members m JOIN fused_entities f "
                "ON f.id=m.fused_entity_id WHERE f.group_id=? AND m.source_id=? "
                "AND COALESCE(m.worker_id,-1)=COALESCE(?,-1) AND m.local_entity_id=? "
                "AND f.ended_at IS NULL ORDER BY m.last_seen_at DESC LIMIT 1",
                (group_id, source_id, row.get("worker_id"), row.get("local_entity_id")),
            )
            key = (-1 if row.get("worker_id") is None else int(row["worker_id"]),
                   row.get("local_entity_id"))
            assert batched.get(key) == (per_row["fused_entity_id"] if per_row else None)
        assert batched, "the scene should have produced memberships to compare"


def test_batched_trajectory_preload_matches_the_per_pair_query(fused_scene):
    fused_ids = [row["id"] for row in db.q("SELECT id FROM fused_entities ORDER BY id")]
    assert fused_ids
    preloaded = multiview._load_recent_fused_observations(fused_ids)
    at_ts = fused_scene["base"] + 0.35
    for fused_id in fused_ids:
        per_pair = db.q(
            "SELECT ts,x_map,y_map FROM fused_observations WHERE fused_entity_id=? "
            "ORDER BY ts DESC,id DESC LIMIT 2", (fused_id,))
        assert [(row["ts"], row["x_map"], row["y_map"]) for row in preloaded.get(fused_id, [])] \
            == [(row["ts"], row["x_map"], row["y_map"]) for row in per_pair]
        # And the prediction built from them is identical.
        fallback = (1.0, 2.0)
        assert multiview._predicted_position(preloaded.get(fused_id, []), at_ts, fallback) \
            == _legacy_predicted_position(per_pair, at_ts, fallback)


def _legacy_predicted_position(rows, at_ts, fallback):
    """The pre-refactor body of `_predicted_position`, verbatim."""
    if len(rows) < 2 or rows[0]["ts"] <= rows[1]["ts"]:
        return fallback
    dt = rows[0]["ts"] - rows[1]["ts"]
    vx = (rows[0]["x_map"] - rows[1]["x_map"]) / dt
    vy = (rows[0]["y_map"] - rows[1]["y_map"]) / dt
    horizon = max(0.0, min(at_ts - rows[0]["ts"], 2.0))
    return rows[0]["x_map"] + vx * horizon, rows[0]["y_map"] + vy * horizon


def test_batched_member_evidence_matches_the_per_entity_join(fused_scene):
    fresh_since = fused_scene["base"] - fused_scene["group"]["track_age_s"]
    entities = db.q("SELECT id FROM fused_entities WHERE ended_at IS NULL ORDER BY id")
    assert entities
    batched = multiview._fresh_members_by_entity([row["id"] for row in entities], fresh_since)
    for row in entities:
        per_entity = db.q(
            "SELECT m.*,c.x_map,c.y_map,c.zone_id,c.confidence,c.ts FROM fused_entity_members m "
            "JOIN source_current_entities c ON c.source_id=m.source_id "
            "AND COALESCE(c.worker_id,-1)=COALESCE(m.worker_id,-1) "
            "AND COALESCE(c.local_entity_id,'')=COALESCE(m.local_entity_id,'') "
            "AND c.sample_key=m.sample_key WHERE m.fused_entity_id=? AND c.ts>=? "
            "AND c.x_map IS NOT NULL AND c.y_map IS NOT NULL ORDER BY c.source_id",
            (row["id"], fresh_since))
        assert batched.get(row["id"], []) == per_entity


# --------------------------------------------------------------------------
# Assignment solver
# --------------------------------------------------------------------------

def _reference_assignment(costs):
    """Brute force over every injective row->column mapping, including partial.

    Encodes the solver's contract explicitly: a valid pair is worth a large
    fixed reward, so more matches always beat fewer; among equal totals the
    lexicographically smallest pair tuple wins; non-finite costs are forbidden.
    """
    rows, columns = len(costs), len(costs[0]) if costs else 0
    best = (0.0, ())

    def search(row, used, total, pairs):
        nonlocal best
        if row == rows:
            if total < best[0] or (total == best[0] and pairs < best[1]):
                best = (total, pairs)
            return
        search(row + 1, used, total, pairs)
        for column in range(columns):
            if used & (1 << column) or not math.isfinite(costs[row][column]):
                continue
            search(row + 1, used | (1 << column),
                   total + costs[row][column] - 1_000_000.0,
                   pairs + ((row, column, costs[row][column]),))

    search(0, 0, 0.0, ())
    return list(best[1])


def test_the_solver_matches_brute_force_on_random_small_matrices():
    generator = random.Random(20260816)
    for _ in range(300):
        rows = generator.randint(1, 4)
        columns = generator.randint(1, 4)
        costs = [[generator.choice([math.inf, round(generator.uniform(0, 2), 3)])
                  for _ in range(columns)] for _ in range(rows)]
        assert minimum_cost_assignment(costs) == _reference_assignment(costs), costs


def test_the_solver_prefers_more_matches_over_a_cheaper_single_match():
    """The gate-sized reward is load-bearing, not an implementation detail."""
    assert minimum_cost_assignment([[0.01, 1.4], [math.inf, 1.5]]) == [
        (0, 0, 0.01), (1, 1, 1.5)]


def test_the_solver_leaves_a_row_unmatched_when_every_pair_is_gated_out():
    assert minimum_cost_assignment([[math.inf, math.inf]]) == []
    assert minimum_cost_assignment([[math.inf, 0.5], [math.inf, math.inf]]) == [(0, 1, 0.5)]


def test_the_solver_breaks_ties_deterministically():
    costs = [[1.0, 1.0], [1.0, 1.0]]
    first = minimum_cost_assignment(costs)
    assert first == [(0, 0, 1.0), (1, 1, 1.0)]
    assert all(minimum_cost_assignment(costs) == first for _ in range(20))


def test_the_solver_handles_the_degenerate_shapes():
    assert minimum_cost_assignment([]) == []
    assert minimum_cost_assignment([[]]) == []


def test_decomposition_matches_the_undecomposed_exact_solver():
    """The reductions must be invisible: same pairs, same tie-breaks, always."""
    generator = random.Random(4711)
    for _ in range(400):
        rows = generator.randint(1, 6)
        columns = generator.randint(1, 10)
        density = generator.choice([0.15, 0.35, 0.7])
        costs = [[round(generator.uniform(0, 2), 3) if generator.random() < density
                  else math.inf for _ in range(columns)] for _ in range(rows)]
        assert minimum_cost_assignment(costs) == multiview._exact_assignment(costs), costs


def test_decomposition_makes_a_realistically_sparse_matrix_tractable():
    """The shape that used to take a minute and a half now finishes at once."""
    import time
    generator = random.Random(99)
    rows, columns = 12, 30
    # Each track is within the spatial gate of at most two existing identities —
    # the situation a metres-wide gate actually produces.
    costs = [[math.inf] * columns for _ in range(rows)]
    for row in range(rows):
        for column in {(row * 2) % columns, (row * 2 + 1) % columns}:
            costs[row][column] = round(generator.uniform(0, 1), 3)
    started = time.perf_counter()
    result = minimum_cost_assignment(costs)
    elapsed = time.perf_counter() - started
    assert elapsed < 0.5, f"sparse 12x30 assignment took {elapsed:.2f}s"
    assert len(result) == rows
    assert len({column for _row, column, _cost in result}) == rows


def test_a_fully_dense_block_is_still_the_exponential_case():
    """Documented limit: decomposition helps sparsity, not density."""
    import time
    costs = [[float(row + column) for column in range(14)] for row in range(6)]
    started = time.perf_counter()
    result = minimum_cost_assignment(costs)
    assert result == multiview._exact_assignment(costs)
    # Not an assertion about being fast — an assertion about where the wall is.
    assert time.perf_counter() - started < 30
