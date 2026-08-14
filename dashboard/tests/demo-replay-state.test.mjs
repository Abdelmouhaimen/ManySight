import test from "node:test";
import assert from "node:assert/strict";
import {
  alertsAt, derivedSampleIndexAt, frameIndexAt, fusedRuntimeIdForSourceTrack,
  interpolateFusedEntities, replayStateAt,
} from "../src/demo-replay-state.js";

const timeline = [
  { index: 0, video_time_s: 0, kpi: { value: 1 }, alert_events: [], fused_entities: [
    { fused_entity_id: "F1", point_map: { x: 0, y: 2 }, members: [
      { source_key: "cam3", local_entity_id: "17" },
      { source_key: "cam4", local_entity_id: "8" },
    ] },
    { fused_entity_id: "gone", point_map: { x: 5, y: 5 } },
  ] },
  { index: 1, video_time_s: .1, kpi: { value: 3 }, alert_events: [{ title: "threshold" }], fused_entities: [
    { fused_entity_id: "F1", point_map: { x: 2, y: 4 } },
    { fused_entity_id: "new", point_map: { x: 8, y: 8 } },
  ] },
];

test("one master time selects exact native frame and latest derived sample", () => {
  assert.equal(frameIndexAt(20, 30, 602), 600);
  assert.equal(derivedSampleIndexAt(timeline, .099), 0);
  assert.equal(derivedSampleIndexAt(timeline, .1), 1);
  const state = replayStateAt({ metadata: { source_fps: 30, media: { a: { frame_count: 602 } } }, timeline }, .05, 2);
  assert.equal(state.frameIndex, 1);
  assert.equal(state.kpi.value, 1); // discrete analytical truth is never interpolated
});

test("positions interpolate only for identities confirmed in both samples", () => {
  const entities = interpolateFusedEntities(timeline, 0, .05, 4);
  assert.deepEqual(entities.find((item) => item.fused_entity_id === "F1").point_map, { x: 1, y: 3 });
  assert.deepEqual(entities.find((item) => item.fused_entity_id === "gone").point_map, { x: 5, y: 5 });
  assert.equal(entities.some((item) => item.fused_entity_id === "new"), false);
  assert.equal(entities[0].runtime_id, "e4:F1");
  assert.equal(fusedRuntimeIdForSourceTrack(entities, "cam3", 17), "e4:F1");
  assert.equal(fusedRuntimeIdForSourceTrack(entities, "cam4", "8"), "e4:F1");
  assert.equal(fusedRuntimeIdForSourceTrack(entities, "cam1", "17"), null);
});

test("alerts are stepwise and epoch namespaced", () => {
  assert.equal(alertsAt(timeline, .09, 0).length, 0);
  assert.equal(alertsAt(timeline, .1, 7)[0].runtime_id, "e7:a1:0");
});
