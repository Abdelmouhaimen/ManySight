import test from "node:test";
import assert from "node:assert/strict";

import {
  ALERT_REVIEW_STATES, CURRENT_KINDS, LEGACY_KINDS, alertStatus, calibrationStatus,
  combinedTrackingStatus, countLabel, dataHealth, isLegacyKind, isOpenAlert, kindLabel,
  placementStatus, resultQuality, resultValue, ruleStatus, runtimeStatus, setupStatus,
  trustworthyCount,
} from "../src/status.js";

test("source data health uses one word per meaning", () => {
  assert.equal(dataHealth({ observation_status: "active" }).label, "Live");
  // "recent" and "active" differ only by ingestion age; both mean data is arriving.
  assert.equal(dataHealth({ observation_status: "recent" }).label, "Live");
  assert.equal(dataHealth({ observation_status: "stale" }).label, "Stale");
  assert.equal(dataHealth({ observation_status: "never" }).label, "No data");
  assert.equal(dataHealth({}).label, "No data");
  assert.equal(dataHealth(null).label, "No data");
});

test("data health never reports a problem tone for a working source", () => {
  assert.equal(dataHealth({ observation_status: "active" }).tone, "good");
  assert.equal(dataHealth({ observation_status: "recent" }).tone, "good");
  assert.equal(dataHealth({ observation_status: "stale" }).tone, "warn");
  // Never having sent data is not an error; it is an unconfigured state.
  assert.equal(dataHealth({ observation_status: "never" }).tone, "idle");
});

test("setup status separates placement from calibration", () => {
  const ready = { placement: { x: 1, y: 1 }, calibrated: true };
  assert.equal(setupStatus(ready).label, "Ready");
  assert.equal(setupStatus({ placement: null, calibrated: true }).label, "Needs setup");
  assert.match(setupStatus({ placement: null, calibrated: false }).help, /floor map/);
  assert.match(setupStatus({ placement: { x: 0, y: 0 }, calibrated: false }).help, /calibrated/);
  assert.equal(placementStatus(ready).label, "Ready");
  assert.equal(calibrationStatus({ calibrated: false }).label, "Needs setup");
});

test("worker runtime collapses six API states into three words", () => {
  assert.equal(runtimeStatus({ effective_status: "running" }).label, "Running");
  assert.equal(runtimeStatus({ effective_status: "starting" }).label, "Running");
  assert.equal(runtimeStatus({ effective_status: "stopped" }).label, "Stopped");
  assert.equal(runtimeStatus({ effective_status: "error" }).label, "Error");
  assert.equal(runtimeStatus({ effective_status: "stale" }).label, "Error");
  assert.match(runtimeStatus({ effective_status: "stale" }).help, /heartbeat/);
  // No worker at all is a dash, not "unreported" or "not running".
  assert.equal(runtimeStatus(null).label, "—");
  assert.equal(runtimeStatus(null).tone, "idle");
});

test("an unknown result is never presented as a known zero", () => {
  assert.equal(resultQuality("unknown").hasValue, false);
  assert.equal(resultValue(0, "unknown"), "—");
  assert.equal(resultValue(3, "unknown"), "—", "even a stored value is not shown as fact");
  assert.equal(resultQuality("unknown").label, "Unknown");
  assert.match(resultQuality("unknown").help, /not a zero/);
});

test("known and partial results keep their value, with partial flagged", () => {
  assert.equal(resultValue(0, "known"), 0, "a confident zero is still a zero");
  assert.equal(resultValue(2, "known"), 2);
  assert.equal(resultQuality("known").label, "Known");
  assert.equal(resultValue(2, "partial"), 2);
  assert.equal(resultQuality("partial").label, "Partial coverage");
  assert.equal(resultQuality("partial").tone, "warn");
  assert.match(resultQuality("partial").help, /undercount/);
});

test("alert rule and review states use the documented vocabulary", () => {
  assert.equal(ruleStatus({ enabled: true }).label, "Enabled");
  assert.equal(ruleStatus({ enabled: false }).label, "Paused");
  assert.equal(alertStatus({ status: "new" }).label, "New");
  assert.equal(alertStatus({ status: "in_review" }).label, "In review");
  assert.equal(alertStatus({ status: "resolved" }).label, "Resolved");
  assert.equal(alertStatus({ status: "dismissed" }).label, "Dismissed");
  assert.equal(alertStatus({}).label, "New", "a status-less alert is new");
  assert.equal(alertStatus({ acknowledged: true }).label, "Resolved");
  assert.deepEqual(ALERT_REVIEW_STATES.map(([value]) => value),
    ["new", "in_review", "resolved", "dismissed"]);
  assert.equal(isOpenAlert({ status: "new" }), true);
  assert.equal(isOpenAlert({ status: "in_review" }), true);
  assert.equal(isOpenAlert({ status: "resolved" }), false);
});

test("combined tracking readiness answers the human question", () => {
  const cameras = [
    { id: 1, calibrated: true, observation_status: "active" },
    { id: 2, calibrated: true, observation_status: "active" },
  ];
  const group = { source_ids: [1, 2], enabled: true };
  assert.equal(combinedTrackingStatus(group, cameras).label, "Ready");
  assert.equal(combinedTrackingStatus(null, cameras).label, "Not configured");
  assert.equal(combinedTrackingStatus({ ...group, enabled: false }, cameras).label, "Paused");

  const halfCalibrated = [cameras[0], { id: 2, calibrated: false, observation_status: "active" }];
  const needsSetup = combinedTrackingStatus(group, halfCalibrated);
  assert.equal(needsSetup.label, "Needs setup");
  assert.match(needsSetup.help, /1 camera still needs calibration/);

  const halfQuiet = [cameras[0], { id: 2, calibrated: true, observation_status: "stale" }];
  const partial = combinedTrackingStatus(group, halfQuiet);
  assert.equal(partial.label, "Partial");
  assert.match(partial.help, /1 of 2 cameras/);
});

test("observation kinds separate the current contract from retired ones", () => {
  assert.deepEqual(CURRENT_KINDS, ["detection", "measurement", "state"]);
  for (const kind of ["zone_enter", "zone_exit", "zone_dwell", "state_change", "count"]) {
    assert.ok(LEGACY_KINDS.includes(kind), `${kind} is retired`);
    assert.equal(isLegacyKind(kind), true);
  }
  assert.equal(isLegacyKind("detection"), false);
  assert.equal(kindLabel("detection"), "Detection");
  assert.equal(kindLabel("zone_enter"), "Zone enter");
  assert.equal(kindLabel(null), "—");
});

test("an untrustworthy observation count shows a dash, never a confident zero", () => {
  // jobs.event_count only rises for submissions carrying a job_id, so a running
  // worker posting detection samples without one reads as zero.
  assert.equal(trustworthyCount(0, { hasRuntime: true }), "—");
  assert.equal(trustworthyCount(0, { hasRuntime: false }), "0");
  assert.equal(trustworthyCount(1200, { hasRuntime: true }), (1200).toLocaleString());
  assert.equal(trustworthyCount(null, { hasRuntime: true }), "—");
});

test("countLabel pluralises", () => {
  assert.equal(countLabel(1, "camera"), "1 camera");
  assert.equal(countLabel(3, "camera"), "3 cameras");
  assert.equal(countLabel(2, "person", "people"), "2 people");
});
