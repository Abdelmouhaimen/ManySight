import assert from "node:assert/strict";
import test from "node:test";

import {
  frameIsStale,
  latestFrameTracks,
  reconcileCompletedFrames,
} from "../src/live-state.js";

function detection(sourceId, entityId, ts = 1) {
  return {
    id: `${sourceId}-${entityId}-${ts}`,
    source_id: sourceId,
    worker_id: 9,
    entity_id: entityId,
    entity_type: "person",
    ts,
    geometry: { point_map: { x: sourceId, y: ts } },
  };
}

function frame(sourceId, timestamp, ids, extra = {}) {
  return {
    source_id: sourceId,
    timestamp,
    expected_count: ids.length,
    source_last_ingestion_at: timestamp,
    detections: ids.map((id) => detection(sourceId, id, timestamp)),
    ...extra,
  };
}

test("latest frame renders its detections", () => {
  assert.deepEqual(
    latestFrameTracks([frame(1, 10, ["A", "B"])]).map((track) => track.position.observation.entity_id),
    ["A", "B"],
  );
});

test("zero frame clears a source and a newer frame replaces old entities", () => {
  let state = reconcileCompletedFrames([], [frame(1, 10, ["A", "B"])]);
  state = reconcileCompletedFrames(state, [frame(1, 11, [])]);
  assert.equal(latestFrameTracks(state).length, 0);
  state = reconcileCompletedFrames(state, [frame(1, 12, ["C"])]);
  assert.deepEqual(latestFrameTracks(state).map((track) => track.position.observation.entity_id), ["C"]);
});

test("no frame and older updates preserve the latest scene", () => {
  const initial = [frame(1, 10, ["A", "B"])];
  assert.deepEqual(reconcileCompletedFrames(initial, []), initial);
  const result = reconcileCompletedFrames(initial, [frame(1, 9, ["old"])]);
  assert.deepEqual(result, initial);
});

test("duplicate updates and duplicate track rows do not duplicate entities", () => {
  const duplicate = frame(1, 10, ["A"]);
  duplicate.detections.push(detection(1, "A", 10));
  const state = reconcileCompletedFrames([duplicate], [duplicate]);
  assert.equal(latestFrameTracks(state).length, 1);
});

test("sources remain independent even when track ids match", () => {
  let state = reconcileCompletedFrames([], [frame(1, 10, ["A"]), frame(2, 10, ["A"])]);
  assert.equal(latestFrameTracks(state).length, 2);
  state = reconcileCompletedFrames(state, [frame(1, 11, [])]);
  assert.deepEqual(latestFrameTracks(state).map((track) => track.position.observation.source_id), [2]);
});

test("staleness never removes latest-frame contents", () => {
  const staleFrame = frame(1, 10, ["A", "B"]);
  assert.equal(frameIsStale(staleFrame, 50), true);
  assert.equal(latestFrameTracks([staleFrame]).length, 2);
});
