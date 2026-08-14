import test from "node:test";
import assert from "node:assert/strict";

import {
  COMPLETION_DWELL_MS, REVEAL_STAGGER_MS, STEP_TIMEOUT_MS, TOUR_GROUPS, WATCH_MIN_MS,
  advanceTour, applyTourAction, chooseTourBranch, evaluateTour, initialTourState,
  observeDemoState, planFor, reportTourTargetMissing, restoreTourState, retryTourStep,
  serializeTourState, skipTourStep, tourCardView, tourChecklist, tourStepById,
} from "../src/demo-tour-model.js";
import {
  demoSession, demoWorkspace, replayCache, replayState, run,
} from "./demo-tour-fixtures.mjs";

function observed(extra = {}) {
  return observeDemoState({
    session: demoSession(), cache: replayCache(), replay: replayState(),
    workspace: demoWorkspace(), ...extra,
  });
}

test("the tour starts on the prepared-workspace step and reflects real session state", () => {
  const state = initialTourState("session-1", 0);
  assert.equal(state.stepId, "workspace");
  assert.equal(state.status, "loading");
  const snapshot = observed();
  assert.equal(snapshot.workspaceReady, true);
  assert.equal(snapshot.sourceCount, 4);
  assert.equal(snapshot.zoneName, "Aisle 04");
  assert.deepEqual(tourChecklist(state).map((row) => row.status), TOUR_GROUPS.map(() => "pending"));
});

test("an automatic step stays on screen long enough to read, then advances", () => {
  const minMs = tourStepById("workspace").minMs;
  assert.ok(minMs >= 1000, "steps are paced for a first-time viewer");
  let state = initialTourState("session-1", 0);
  state = evaluateTour(state, observed(), minMs - 1);
  assert.equal(state.status, "loading", "already-true work is still held on screen");
  state = evaluateTour(state, observed(), minMs);
  assert.equal(state.status, "complete");
  assert.equal(state.stepId, "workspace", "the finished step stays visible");
  state = evaluateTour(state, observed(), minMs + COMPLETION_DWELL_MS - 1);
  assert.equal(state.stepId, "workspace");
  state = evaluateTour(state, observed(), minMs + COMPLETION_DWELL_MS);
  assert.equal(state.stepId, "sources");
  assert.equal(state.status, "loading");
});

test("source progress sequences the real source IDs instead of inventing them", () => {
  const state = { ...initialTourState("session-1", 0), stepId: "sources", status: "loading" };
  const first = tourCardView(state, observed(), 0);
  assert.deepEqual(first.detail.map((item) => item.status),
    ["active", "pending", "pending", "pending"]);
  assert.deepEqual(first.detail.map((item) => item.label),
    ["Camera 1", "Camera 2", "Camera 3", "Camera 4"]);
  const later = tourCardView(state, observed(), REVEAL_STAGGER_MS * 3);
  assert.deepEqual(later.detail.map((item) => item.status),
    ["complete", "complete", "complete", "active"]);
  assert.equal(evaluateTour(state, observed(), REVEAL_STAGGER_MS * 3).status, "complete");
});

test("a source that does not exist yet is never presented as created", () => {
  const partial = observeDemoState({
    session: demoSession({ result: { ...demoSession().result, source_ids: {} } }),
    cache: replayCache(), workspace: { sources: [] },
  });
  const state = { ...initialTourState("session-1", 0), stepId: "sources", status: "loading" };
  assert.deepEqual(tourCardView(state, partial, 5000).detail, []);
  assert.equal(evaluateTour(state, partial, 5000).status, "loading");
});

test("both setup paths converge on the same later walkthrough", () => {
  const auto = planFor("auto").map((step) => step.id);
  const manual = planFor("manual").map((step) => step.id);
  assert.deepEqual(auto.slice(auto.indexOf("tracking")), manual.slice(manual.indexOf("tracking")));
  assert.deepEqual(auto.filter((id) => id.startsWith("space-")), ["space-choice", "space-auto"]);
  assert.deepEqual(manual.filter((id) => id.startsWith("space-")),
    ["space-choice", "space-open-digitizer", "space-trace", "space-restore"]);
  assert.equal(auto.includes("space-open-digitizer"), false);
  assert.equal(manual.includes("space-auto"), false);
});

test("the physical-space choice branches the teaching path only", () => {
  const base = { ...initialTourState("session-1", 0), stepId: "space-choice", status: "waiting_for_user" };
  assert.equal(applyTourAction(base, "auto", 10).stepId, "space-auto");
  assert.equal(applyTourAction(base, "auto", 10).branch, "auto");
  assert.equal(applyTourAction(base, "manual", 10).stepId, "space-open-digitizer");
  assert.equal(applyTourAction(base, "manual", 10).branch, "manual");
  assert.equal(applyTourAction(base, "nonexistent", 10).stepId, "space-choice");
});

test("a required interaction waits for the real UI event and never self-advances", () => {
  let state = chooseTourBranch(
    { ...initialTourState("session-1", 0), stepId: "space-choice" }, "manual", 0,
  );
  assert.equal(state.stepId, "space-open-digitizer");
  assert.equal(state.status, "waiting_for_user");
  const idle = run(state, () => observed(), { maxMs: 30000 });
  assert.equal(idle.state.stepId, "space-open-digitizer", "no event, no progress");
  const opened = evaluateTour(state, observed({ events: { digitizerOpen: true } }), 500);
  assert.equal(opened.status, "complete");
  assert.equal(advanceTour(opened, 600).stepId, "space-trace");
});

test("digitizer guidance follows the real trace and completes on a real save", () => {
  const state = { ...initialTourState("session-1", 0), branch: "manual", stepId: "space-trace",
    status: "waiting_for_user" };
  const drawing = observed({ events: { digitizerOpen: true, digitizer: { polygons: 1, scalePoints: 1 } } });
  const view = tourCardView(state, drawing, 0);
  assert.deepEqual(view.detail.map((item) => [item.label, item.status]), [
    ["Draw the space outline", "complete"],
    ["Set the physical scale", "active"],
    ["Save the plan", "pending"],
  ]);
  assert.equal(evaluateTour(state, drawing, 1000).status, "waiting_for_user");
  const saved = observed({ events: { planSavedAt: 123 } });
  assert.equal(evaluateTour(state, saved, 1000).status, "complete");
  assert.equal(tourCardView(state, saved, 0).hint, "",
    "a saved plan needs no reopen hint");
  assert.match(tourCardView(state, observed(), 0).hint, /Reopen/);
});

test("the practice detour restores prepared geometry before continuing", () => {
  const step = tourStepById("space-restore");
  assert.equal(step.effect, "restorePracticeSpace");
  const state = { ...initialTourState("session-1", 0), branch: "manual", stepId: "space-restore",
    status: "loading" };
  const held = step.minMs;
  assert.equal(evaluateTour(state, observed({ events: { planSavedAt: 1 } }), held).status, "loading");
  const restored = observed({ events: { planSavedAt: 1, planRestoredAt: 2 } });
  assert.equal(evaluateTour(state, restored, held - 1).status, "loading");
  assert.equal(evaluateTour(state, restored, held).status, "complete");
});

test("manual calibration teaches Camera 1 and reports the remaining validated cameras", () => {
  const open = { ...initialTourState("session-1", 0), branch: "manual", stepId: "calibration-open",
    status: "waiting_for_user" };
  assert.equal(tourCardView(open, observed(), 0).spotlightTarget, "camera-calibrate-1");
  assert.equal(evaluateTour(open, observed({ events: { calibrationOpenFor: 2 } }), 100).status,
    "waiting_for_user", "another camera is not the taught one");
  assert.equal(evaluateTour(open, observed({ events: { calibrationOpenFor: 1 } }), 100).status, "complete");

  const practice = { ...open, stepId: "calibration-practice" };
  const pairs = observed({ events: { calibrationOpenFor: 1, calibrationPairs: 4 } });
  assert.deepEqual(tourCardView(practice, pairs, 0).detail.map((item) => item.label),
    ["4/4 point pairs matched", "Compute & save"]);
  assert.equal(evaluateTour(practice, pairs, 100).status, "waiting_for_user");
  assert.equal(
    evaluateTour(practice, observed({ events: { practiceCalibrationAt: 9 } }), 100).status,
    "complete",
  );

  const rest = { ...open, stepId: "calibration-rest", status: "loading" };
  assert.deepEqual(tourCardView(rest, observed(), REVEAL_STAGGER_MS * 2).detail.map((item) => item.label),
    ["Camera 2 calibrated", "Camera 3 calibrated", "Camera 4 calibrated"]);
  const uncalibrated = observed({ workspace: demoWorkspace({ calibrated: false }) });
  assert.equal(evaluateTour(rest, uncalibrated, 5000).status, "loading",
    "uncalibrated cameras are never shown as calibrated");
});

test("prepared tracking data is described truthfully, without claiming live inference", () => {
  const state = { ...initialTourState("session-1", 0), stepId: "tracking", status: "loading" };
  const view = tourCardView(state, observed(), REVEAL_STAGGER_MS * 4);
  assert.deepEqual(view.detail.map((item) => item.label), [
    "Camera 1 tracking data", "Camera 2 tracking data", "Camera 3 tracking data",
    "Camera 4 tracking data", "Multi-camera replay ready",
  ]);
  assert.match(view.description, /prerecorded/);
  assert.equal(evaluateTour(state, observed(), REVEAL_STAGGER_MS * 4).status, "complete");
  const withoutCache = observed({ cache: replayCache({ timeline: [] }) });
  assert.equal(evaluateTour(state, withoutCache, 5000).status, "loading");
});

test("the Codex step is explicitly a simulated request, not an agent run", () => {
  const state = { ...initialTourState("session-1", 0), stepId: "codex-request",
    status: "waiting_for_user" };
  const view = tourCardView(state, observed(), 0);
  assert.match(view.title, /imagine you asked Codex/i);
  assert.match(view.quote, /at least 2 people in Aisle 04/i);
  assert.match(view.description, /reproduce/i);
  assert.match(view.description, /Nothing is running an agent/i);
  assert.deepEqual(view.actions.map((action) => action.label), ["Continue"]);
  assert.equal(view.detail.length, 0, "no fabricated tool output");
  assert.equal(applyTourAction(state, "continue", 10).stepId, "zone-camera-3");
});

test("no walkthrough copy claims that Codex is executing", () => {
  const snapshot = observed({ replay: replayState({ alerts: [{ message: "x", video_time_s: 1 }] }) });
  const forbidden = /Codex (is|was) (inspecting|thinking|calling|deciding|running)|Codex decided|tool call|chain of thought/i;
  for (const branch of ["auto", "manual"]) {
    for (const step of planFor(branch)) {
      const view = tourCardView(
        { ...initialTourState("session-1", 0), branch, stepId: step.id, status: "loading" },
        snapshot, 0,
      );
      const copy = [view.title, view.description, view.quote,
        ...view.detail.map((item) => item.label)].join(" ");
      assert.doesNotMatch(copy, forbidden, `step ${step.id} must not claim agent execution`);
    }
  }
});

test("the Aisle 04 sequence spotlights both contributing cameras, then the canonical zone", () => {
  const targets = ["zone-camera-3", "zone-camera-4", "zone-canonical"].map((stepId) =>
    tourCardView({ ...initialTourState("session-1", 0), stepId, status: "loading" }, observed(), 0));
  assert.deepEqual(targets.map((view) => view.spotlightTarget),
    ["camera-3-tile", "camera-4-tile", "floor-map"]);
  assert.deepEqual(targets.map((view) => view.route), ["demo", "demo", "setup"]);
  const withoutViews = observed({ workspace: demoWorkspace({ zoneViews: false }),
    session: demoSession({ action_log: [] }) });
  assert.equal(
    evaluateTour({ ...initialTourState("s", 0), stepId: "zone-camera-3", status: "loading" },
      withoutViews, 100).status,
    "loading",
  );
});

test("query, alert, and dashboard steps report the real demo objects", () => {
  const snapshot = observed();
  const view = (stepId) => tourCardView(
    { ...initialTourState("session-1", 0), stepId, status: "loading" }, snapshot, 0,
  );
  assert.equal(view("query").detail[0].label, "People in Aisle 04");
  assert.match(view("alert").description, /People in Aisle 04 >= 2/);
  assert.equal(view("dashboard").detail[0].label, "Fused people in Aisle 04");
  assert.equal(view("dashboard").spotlightTarget, "dashboard-kpi");
  const missing = observeDemoState({ session: demoSession(), cache: replayCache(), workspace: {} });
  assert.equal(missing.alertCondition, "", "no invented threshold when the rule is unread");
});

test("the alert step uses the recorded event rather than a hardcoded count", () => {
  const state = { ...initialTourState("session-1", 0), stepId: "watching", status: "loading" };
  const quiet = observed({ replay: replayState({ kpi: { value: 1, quality: "known" } }) });
  assert.equal(evaluateTour(state, quiet, 60000).status, "loading", "watching never times out");
  assert.equal(tourCardView(state, quiet, 0).dims, false, "playback is not dimmed");
  const fired = observed({
    cache: replayCache({
      timeline: [
        { index: 0, video_time_s: 0, kpi: { value: 1, quality: "known" }, alert_events: [] },
        { index: 1, video_time_s: 7.5, kpi: { value: 3, quality: "known" },
          alert_events: [{ message: "Aisle 04 threshold", video_time_s: 7.5 }] },
      ],
    }),
    replay: replayState({
      kpi: { value: 1, quality: "known" },
      alerts: [{ message: "Aisle 04 threshold", video_time_s: 7.5 }],
    }),
  });
  assert.equal(evaluateTour(state, fired, 100).status, "loading",
    "the replay is watched for a moment before the card reacts");
  assert.equal(evaluateTour(state, fired, WATCH_MIN_MS).status, "complete");
  const reached = tourCardView(
    { ...state, stepId: "alert-reached", status: "waiting_for_user" }, fired, 0,
  );
  assert.match(reached.description, /3 people were in Aisle 04 when the recorded alert fired/,
    "the qualifying count comes from the sample that fired, not the live KPI");
  const single = observed({
    cache: replayCache({
      timeline: [{ index: 0, video_time_s: 2, kpi: { value: 1, quality: "known" },
        alert_events: [{ message: "one", video_time_s: 2 }] }],
    }),
    replay: replayState({ kpi: { value: 5 }, alerts: [{ message: "one", video_time_s: 2 }] }),
  });
  assert.match(
    tourCardView({ ...state, stepId: "alert-reached", status: "waiting_for_user" }, single, 0).description,
    /1 person was in Aisle 04/,
  );
  assert.deepEqual(reached.actions.map((action) => action.id), ["explore", "exit"]);
});

test("a stalled automatic step reports an error and offers recovery", () => {
  const blank = observeDemoState({});
  const state = { ...initialTourState("session-1", 0), stepId: "query", status: "loading" };
  assert.equal(evaluateTour(state, blank, STEP_TIMEOUT_MS).status, "loading");
  const failed = evaluateTour(state, blank, STEP_TIMEOUT_MS + 1);
  assert.equal(failed.status, "error");
  assert.match(failed.error, /did not complete/);
  const view = tourCardView(failed, blank, 0);
  assert.deepEqual(view.actions, [], "recovery replaces the step actions");
  assert.match(view.error, /did not complete/);
  assert.equal(retryTourStep(failed, 999).status, "loading");
  assert.equal(skipTourStep(failed, 999).stepId, "alert");
  assert.deepEqual(
    tourChecklist(failed).find((row) => row.id === "query").status, "error",
  );
});

test("a missing spotlight target is reported instead of silently advancing", () => {
  const state = { ...initialTourState("session-1", 0), branch: "manual",
    stepId: "space-open-digitizer", status: "waiting_for_user" };
  const missing = reportTourTargetMissing(state, "Digitize plan is not on screen.");
  assert.equal(missing.status, "error");
  assert.equal(missing.stepId, "space-open-digitizer");
  assert.equal(evaluateTour(missing, observed(), 60000).stepId, "space-open-digitizer");
  assert.equal(tourStepById("calibration-open").fallback.length > 0, true);
});

test("tour state survives navigation and a browser refresh", () => {
  const state = { ...initialTourState("session-1", 0), branch: "manual",
    stepId: "calibration-practice", status: "waiting_for_user", minimized: true };
  const restored = restoreTourState(serializeTourState(state), "session-1", 500);
  assert.equal(restored.stepId, "calibration-practice");
  assert.equal(restored.branch, "manual");
  assert.equal(restored.minimized, true);
  assert.equal(restored.status, "waiting_for_user");
  assert.equal(restoreTourState(serializeTourState(state), "another-session", 0), null);
  assert.equal(restoreTourState("{not json", "session-1", 0), null);
  assert.equal(restoreTourState(JSON.stringify({ version: 1, sessionId: "session-1",
    stepId: "calibration-practice", branch: "auto" }), "session-1", 0), null,
  "a branch step cannot be restored into the other branch");
  assert.equal(restoreTourState(JSON.stringify({ version: 1, sessionId: "session-1",
    stepId: "removed-step" }), "session-1", 0), null);
});

test("the tour never mutates or drives replay state", () => {
  const session = Object.freeze(demoSession());
  const cache = replayCache();
  const replay = replayState({ alerts: [{ message: "threshold", video_time_s: 0 }] });
  for (const value of [cache, cache.metadata, cache.timeline, replay, replay.kpi, replay.alerts]) {
    Object.freeze(value);
  }
  const snapshot = observeDemoState({ session, cache, replay, workspace: demoWorkspace() });
  let state = initialTourState("session-1", 0);
  for (const branch of [null, "auto"]) {
    state = { ...state, branch };
    for (const step of planFor(branch)) {
      const probe = { ...state, stepId: step.id, status: "loading" };
      tourCardView(probe, snapshot, 1000);
      evaluateTour(probe, snapshot, 1000);
    }
  }
  // Frozen inputs would have thrown on any write; identity proves no rebuild.
  assert.equal(snapshot.kpiValue, replay.kpi.value);
  assert.equal(cache.timeline.length, 1);
  assert.deepEqual(replay.alerts, [{ message: "threshold", video_time_s: 0 }]);
});

test("the automatic path reaches the explore state with every checklist row complete", () => {
  const start = chooseTourBranch(
    { ...initialTourState("session-1", 0), stepId: "space-choice" }, "auto", 0,
  );
  let progress = run(start, () => observed({
    replay: replayState({ kpi: { value: 2, quality: "known" },
      alerts: [{ message: "Aisle 04 threshold", video_time_s: 7.5 }] }),
  }));
  assert.equal(progress.state.stepId, "codex-request");
  progress = run(applyTourAction(progress.state, "continue", progress.now), () => observed({
    replay: replayState({ kpi: { value: 2, quality: "known" },
      alerts: [{ message: "Aisle 04 threshold", video_time_s: 7.5 }] }),
  }), { from: progress.now });
  assert.equal(progress.state.stepId, "ready");
  progress = run(applyTourAction(progress.state, "watch", progress.now), () => observed({
    replay: replayState({ kpi: { value: 2, quality: "known" },
      alerts: [{ message: "Aisle 04 threshold", video_time_s: 7.5 }] }),
  }), { from: progress.now });
  assert.equal(progress.state.stepId, "alert-reached");
  const explore = applyTourAction(progress.state, "explore", progress.now);
  assert.equal(explore.stepId, "explore");
  const view = tourCardView(explore, observed(), 0);
  assert.equal(view.finished, true);
  assert.equal(view.dims, false);
  assert.deepEqual(view.checklist.map((row) => row.status), TOUR_GROUPS.map(() => "complete"));
});

test("the manual path walks the real controls and rejoins the same sequence", () => {
  const start = chooseTourBranch(
    { ...initialTourState("session-1", 0), stepId: "space-choice" }, "manual", 0,
  );
  const events = { digitizerOpen: true, digitizer: { polygons: 1, scalePoints: 2, knownDistance: 5 } };
  let progress = run(start, () => observed());
  assert.equal(progress.state.stepId, "space-open-digitizer", "waits for the real click");
  progress = run(progress.state, () => observed({ events }), { from: progress.now });
  assert.equal(progress.state.stepId, "space-trace");
  const saved = { ...events, planSavedAt: 1, planRestoredAt: 2 };
  progress = run(progress.state, () => observed({ events: saved }), { from: progress.now });
  assert.equal(progress.state.stepId, "calibration-explain");
  progress = run(applyTourAction(progress.state, "continue", progress.now),
    () => observed({ events: saved }), { from: progress.now });
  assert.equal(progress.state.stepId, "calibration-open");
  const practised = { ...saved, calibrationOpenFor: 1, calibrationPairs: 6, practiceCalibrationAt: 3 };
  const opened = run(progress.state, () => observed({ events: practised }), { from: progress.now });
  assert.equal(opened.state.stepId, "calibration-practice");
  progress = run(opened.state, () => observed({ events: practised }), { from: opened.now });
  assert.equal(progress.state.stepId, "codex-request",
    "calibration practice then prepared cameras rejoin the shared walkthrough");
  assert.deepEqual(
    [...opened.visited, ...progress.visited].filter((id) => id.startsWith("calibration-")),
    ["calibration-open", "calibration-practice", "calibration-practice", "calibration-rest"],
  );
});
