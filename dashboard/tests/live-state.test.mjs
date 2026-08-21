import assert from "node:assert/strict";
import test from "node:test";

import {
  availableIdentityMode,
  combinedModeAvailable,
  frameIsStale,
  highlightedZoneIds,
  latestFrameTracks,
  pointInPolygon,
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

test("combined mode is unavailable until a multiview group exists", () => {
  assert.equal(combinedModeAvailable({ groups: [] }), false);
  assert.equal(availableIdentityMode("fused", { groups: [] }), "source");
  assert.equal(combinedModeAvailable({ groups: [{ id: 4 }] }), true);
  assert.equal(availableIdentityMode("fused", { groups: [{ id: 4 }] }), "fused");
  assert.equal(availableIdentityMode("source", { groups: [] }), "source");
});

/* ---------------------------------------------- presentation-only highlight */

const SQUARE = [{ x: 0, y: 0 }, { x: 4, y: 0 }, { x: 4, y: 4 }, { x: 0, y: 4 }];
const ZONES = [{ id: 7 }, { id: 8 }];
const ringsOf = (zone) => (zone.id === 7 ? [SQUARE] : []);
const at = (x, y, observation = {}) => ({ position: { x, y, observation } });

test("pointInPolygon includes the boundary and excludes the outside", () => {
  assert.equal(pointInPolygon({ x: 2, y: 2 }, SQUARE), true);
  assert.equal(pointInPolygon({ x: 0, y: 2 }, SQUARE), true);
  assert.equal(pointInPolygon({ x: 9, y: 2 }, SQUARE), false);
  assert.equal(pointInPolygon({ x: 2, y: 2 }, []), false);
});

test("a zone lights up when a rendered track is inside it", () => {
  assert.deepEqual(highlightedZoneIds([at(2, 2)], ZONES, ringsOf), new Set(["7"]));
});

test("the server's own zone assignment lights the zone even without rings", () => {
  const tracks = [at(99, 99, { zone_id: 8 })];
  assert.deepEqual(highlightedZoneIds(tracks, ZONES, ringsOf), new Set(["8"]));
});

test("nothing lights up without tracks", () => {
  assert.deepEqual(highlightedZoneIds([], ZONES, ringsOf), new Set());
});

/* The guard rail: this helper is a lighting cue. If it ever starts returning a
 * number, something is about to present a browser-side guess as an occupancy
 * value — which is exactly what the server's quality-carrying count is for. */
test("the highlight reports zones to paint, never an occupancy count", () => {
  const result = highlightedZoneIds([at(1, 1), at(2, 2), at(3, 3)], ZONES, ringsOf);
  assert.ok(result instanceof Set, "must stay a Set of zone ids");
  assert.equal(typeof result, "object");
  // Three people stand in one zone and the result still describes one zone.
  assert.deepEqual(result, new Set(["7"]));
});
