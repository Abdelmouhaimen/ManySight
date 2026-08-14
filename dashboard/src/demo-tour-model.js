/* Guided-demo tour state machine.
 *
 * The tour never performs demo work and never decides that demo work happened:
 * every completion condition reads an `observed` snapshot built from the real
 * demo session, the real demo-workspace objects, and the committed replay cache
 * (see observeDemoState). Presentation-only sequencing — revealing four real
 * source IDs one at a time — is explicit and time-based, never invented state.
 *
 * This module is deliberately free of React and DOM access so the whole flow can
 * be exercised without a browser.
 */

/* Pacing is deliberately unhurried: every step is something a first-time viewer
 * is meant to read and watch happen in the real interface. `minMs` on a step
 * holds it on screen for at least that long once its real work is done. */
export const REVEAL_STAGGER_MS = 600;
export const COMPLETION_DWELL_MS = 1900;
export const STEP_TIMEOUT_MS = 25000;
/* The recorded threshold event can already be active when playback starts, so
 * the card watches the real replay for a while before reacting to it. */
export const WATCH_MIN_MS = 4000;

/** Checklist rows shown in the progress card, in order. */
export const TOUR_GROUPS = [
  { id: "sources", label: "Camera sources" },
  { id: "space", label: "Physical space" },
  { id: "calibration", label: "Camera calibration" },
  { id: "tracking", label: "Person tracking" },
  { id: "zone", label: "Aisle 04" },
  { id: "query", label: "Occupancy query" },
  { id: "alert", label: "Alert" },
  { id: "dashboard", label: "Dashboard" },
];

const CHECKLIST_IDS = new Set(TOUR_GROUPS.map((group) => group.id));

function line(label, status) {
  return { label, status };
}

/** Reveal already-created results one at a time instead of all at once. */
function staged(items, elapsedMs, stagger = REVEAL_STAGGER_MS) {
  const reachable = items.filter((item) => item.ready).length;
  const revealed = Math.min(reachable, Math.floor(elapsedMs / stagger) + 1);
  return items.map((item, index) => line(
    item.label,
    !item.ready ? "pending" : index < revealed - 1 ? "complete"
      : index === revealed - 1 ? "active" : "pending",
  ));
}

function stagedComplete(items, elapsedMs, stagger = REVEAL_STAGGER_MS) {
  return items.length > 0 && items.every((item) => item.ready)
    && elapsedMs >= (items.length - 1) * stagger;
}

function cameraItems(observed, ready = (camera) => Boolean(camera.sourceId), suffix = "") {
  return observed.cameras.map((camera) => ({
    label: `${camera.name}${suffix}`,
    ready: ready(camera),
  }));
}

/** Does this camera's zone view exist in the demo workspace yet? */
function zoneCameraAdded(observed, index) {
  return observed.zoneCameras.some((camera) => camera.index === index && camera.viewAdded);
}

function zoneCameraLine(observed, index) {
  const camera = observed.cameras.find((item) => item.index === index);
  return line(`${camera?.name || `Camera ${index}`} view added`,
    zoneCameraAdded(observed, index) ? "complete" : "active");
}

const STEPS = [
  {
    id: "workspace",
    group: "workspace",
    type: "automatic",
    route: "demo",
    minMs: 1400,
    title: "Preparing your demo",
    description: "Creating a temporary StoreLens workspace…",
    detail: (observed) => [line("Demo workspace ready", observed.workspaceReady ? "complete" : "active")],
    complete: (observed) => observed.workspaceReady,
  },
  {
    id: "sources",
    group: "sources",
    type: "automatic",
    route: "demo",
    target: "demo-camera-grid",
    title: "Adding camera sources",
    description: "Each camera becomes one logical StoreLens source.",
    detail: (observed, elapsed) => staged(cameraItems(observed), elapsed),
    complete: (observed, elapsed) => stagedComplete(cameraItems(observed), elapsed)
      && observed.sourceCount === observed.cameras.length,
    completedNote: (observed) => `${observed.sourceCount} demo cameras ready`,
  },
  {
    id: "space-choice",
    group: "space",
    type: "explanation",
    route: "demo",
    title: "Set up the physical space",
    description:
      "StoreLens needs a floor plan so detections from several cameras can share the same physical coordinate system.",
    actions: [
      { id: "manual", label: "Show me how", branch: "manual" },
      { id: "auto", label: "Set it up automatically", branch: "auto", primary: true },
    ],
  },

  // Automatic setup path.
  {
    id: "space-auto",
    group: "space",
    branch: "auto",
    type: "automatic",
    route: "demo",
    minMs: 1800,
    title: "Preparing the physical space",
    description: "Using the prepared demo floor plan and camera positions.",
    detail: (observed, elapsed) => staged([
      { label: "Floor plan ready", ready: observed.storeMapReady },
      { label: "Camera positions ready", ready: observed.placedCameraCount === observed.cameras.length },
    ], elapsed),
    complete: (observed, elapsed) => stagedComplete([
      { label: "", ready: observed.storeMapReady },
      { label: "", ready: observed.placedCameraCount === observed.cameras.length },
    ], elapsed),
  },
  {
    id: "calibration-auto",
    group: "calibration",
    branch: "auto",
    type: "automatic",
    route: "demo",
    title: "Preparing camera calibration",
    description: "Each camera already carries a validated world-to-pixel matrix.",
    detail: (observed, elapsed) => staged(cameraItems(observed, (camera) => camera.calibrated, " calibrated"), elapsed),
    complete: (observed, elapsed) =>
      stagedComplete(cameraItems(observed, (camera) => camera.calibrated), elapsed),
    completedNote: () => "All cameras mapped into one physical space",
  },

  // Manual "Show me how" path.
  {
    id: "space-open-digitizer",
    group: "space",
    branch: "manual",
    type: "user_action",
    route: "setup",
    setupTab: "space",
    target: "digitize-plan",
    title: "Create the physical map",
    description: "Click Digitize plan to begin.",
    complete: (observed) => observed.digitizerOpen || Boolean(observed.planSavedAt),
  },
  {
    id: "space-trace",
    group: "space",
    branch: "manual",
    type: "user_action",
    route: "setup",
    setupTab: "space",
    inModal: true,
    title: "Trace the warehouse floor",
    description: "The bird's-eye plan is already loaded behind the tracing canvas.",
    detail: (observed) => [
      line("Draw the space outline", observed.digitizer.polygons > 0 ? "complete" : "active"),
      line("Set the physical scale", observed.digitizer.scalePoints === 2 && observed.digitizer.knownDistance > 0
        ? "complete" : observed.digitizer.polygons > 0 ? "active" : "pending"),
      line("Save the plan", observed.planSavedAt ? "complete" : "pending"),
    ],
    complete: (observed) => Boolean(observed.planSavedAt),
    reopen: (observed) => !observed.digitizerOpen && !observed.planSavedAt,
  },
  {
    id: "space-restore",
    group: "space",
    branch: "manual",
    type: "automatic",
    route: "setup",
    effect: "restorePracticeSpace",
    minMs: 1800,
    title: "Physical map created",
    description:
      "StoreLens compares your trace, then restores the prepared demo plan so the recorded replay keeps its exact geometry.",
    detail: (observed) => [
      line("Physical map created", observed.planSavedAt ? "complete" : "pending"),
      line("Validated demo plan restored", observed.planRestoredAt ? "complete" : "active"),
    ],
    complete: (observed) => Boolean(observed.planRestoredAt),
  },
  {
    id: "calibration-explain",
    group: "calibration",
    branch: "manual",
    type: "explanation",
    route: "setup",
    setupTab: "space",
    title: "Calibrate one camera",
    description: "Camera pixels need to be related to the physical floor map. You only teach Camera 1.",
    actions: [{ id: "continue", label: "Continue", primary: true }],
  },
  {
    id: "calibration-open",
    group: "calibration",
    branch: "manual",
    type: "user_action",
    route: "setup",
    setupTab: "space",
    target: "camera-calibrate-1",
    fallback: "Select Camera 1 in the Sources panel to reach its calibration control.",
    title: "Open Camera 1 calibration",
    description: "Click Calibrate camera for Camera 1.",
    complete: (observed) => observed.calibrationOpenFor === 1 || Boolean(observed.practiceCalibrationAt),
  },
  {
    id: "calibration-practice",
    group: "calibration",
    branch: "manual",
    type: "user_action",
    route: "setup",
    setupTab: "space",
    inModal: true,
    title: "Match camera points with the floor map",
    description: "Click a fixed floor point in the recorded frame, then the same physical spot on the map.",
    detail: (observed) => [
      line(`${observed.calibrationPairs}/4 point pairs matched`,
        observed.calibrationPairs >= 4 ? "complete" : "active"),
      line("Compute & save", observed.practiceCalibrationAt ? "complete" : "pending"),
    ],
    complete: (observed) => Boolean(observed.practiceCalibrationAt),
    reopen: (observed) => observed.calibrationOpenFor === null && !observed.practiceCalibrationAt,
  },
  {
    id: "calibration-rest",
    group: "calibration",
    branch: "manual",
    type: "automatic",
    route: "setup",
    minMs: 1800,
    title: "Preparing remaining cameras",
    description: "Cameras 2 to 4 use their validated imported calibrations.",
    detail: (observed, elapsed) => staged(
      cameraItems(observed, (camera) => camera.calibrated, " calibrated").slice(1), elapsed,
    ),
    complete: (observed, elapsed) => stagedComplete(
      cameraItems(observed, (camera) => camera.calibrated).slice(1), elapsed,
    ),
    completedNote: () => "All cameras mapped into one physical space",
  },

  // Both paths converge here.
  {
    id: "tracking",
    group: "tracking",
    type: "automatic",
    route: "demo",
    target: "demo-camera-grid",
    title: "Preparing person tracking",
    description: "This demo replays prerecorded tracking results, so it runs without GPU inference.",
    detail: (observed, elapsed) => [
      ...staged(cameraItems(observed, (camera) => camera.trackingFrames > 0, " tracking data"), elapsed),
      line("Multi-camera replay ready", observed.replayReady ? "complete" : "pending"),
    ],
    complete: (observed, elapsed) => observed.replayReady && stagedComplete(
      cameraItems(observed, (camera) => camera.trackingFrames > 0), elapsed,
    ),
  },
  {
    id: "codex-request",
    group: "request",
    type: "explanation",
    route: "demo",
    title: "Now imagine you asked Codex:",
    quote: "Alert me when there are at least 2 people in Aisle 04.",
    description:
      "Nothing is running an agent here. We reproduce the configuration StoreLens would need for that request, using what this demo already prepared.",
    actions: [{ id: "continue", label: "Continue", primary: true }],
  },
  {
    id: "zone-camera-3",
    group: "zone",
    type: "automatic",
    route: "demo",
    target: "camera-3-tile",
    effect: "applyRequest:zone_seed",
    minMs: 3200,
    title: "Creating Aisle 04",
    description: "This camera sees part of the physical area. Watch its floor trace appear.",
    detail: (observed) => [zoneCameraLine(observed, 3)],
    complete: (observed) => zoneCameraAdded(observed, 3),
  },
  {
    id: "zone-camera-4",
    group: "zone",
    type: "automatic",
    route: "demo",
    target: "camera-4-tile",
    effect: "applyRequest:zone_extend",
    minMs: 3200,
    title: "Creating Aisle 04",
    description: "Another camera sees the same physical zone from a different view.",
    detail: (observed) => [zoneCameraLine(observed, 4)],
    complete: (observed) => zoneCameraAdded(observed, 4),
  },
  {
    id: "zone-canonical",
    group: "zone",
    type: "automatic",
    route: "setup",
    setupTab: "space",
    target: "floor-map",
    minMs: 3200,
    title: "One physical zone",
    description: "Both camera views project into the same metric floor area.",
    detail: (observed) => [
      line(`${observed.zoneName || "Zone"} created`, observed.zoneId ? "complete" : "active"),
    ],
    complete: (observed) => Boolean(observed.zoneId)
      && observed.zoneCameras.filter((camera) => camera.viewAdded).length >= 2,
  },
  {
    id: "query",
    group: "query",
    type: "automatic",
    route: "setup",
    effect: "applyRequest:query",
    minMs: 2400,
    title: "Creating occupancy query",
    description: "One saved question, derived centrally by StoreLens.",
    detail: (observed) => [line(observed.queryName || "Occupancy query", observed.queryId ? "complete" : "active")],
    complete: (observed) => Boolean(observed.queryId),
  },
  {
    id: "alert",
    group: "alert",
    type: "automatic",
    route: "setup",
    effect: "applyRequest:alert",
    minMs: 2400,
    title: "Creating alert",
    description: (observed) => observed.alertCondition
      ? `Trigger when: ${observed.queryName || "the saved query"} ${observed.alertCondition}`
      : "Trigger on the saved occupancy query.",
    detail: (observed) => [line("Alert ready", observed.alertRuleId ? "complete" : "active")],
    complete: (observed) => Boolean(observed.alertRuleId),
  },
  {
    id: "dashboard",
    group: "dashboard",
    type: "automatic",
    route: "dashboard",
    target: "dashboard-kpi",
    effect: "applyRequest:dashboard",
    minMs: 2400,
    title: "Creating dashboard",
    description: "The widget is a view over the saved query — not a second calculation.",
    detail: (observed) => [
      line(observed.dashboardWidgetTitle || "Dashboard widget", observed.dashboardId ? "complete" : "active"),
    ],
    complete: (observed) => Boolean(observed.dashboardId),
  },
  {
    id: "ready",
    group: "ready",
    type: "explanation",
    route: "demo",
    title: "Everything is ready",
    description: "Watch StoreLens track the space across four synchronized cameras.",
    actions: [{ id: "watch", label: "Watch it run", primary: true }],
  },
  {
    id: "watching",
    group: "ready",
    type: "automatic",
    route: "demo",
    dim: false,
    timeoutMs: null,
    minMs: WATCH_MIN_MS,
    title: "Replay is running",
    description: "Video, boxes, fused positions, KPI, and alerts follow one master clock.",
    detail: (observed) => [
      line(`Fused people in ${observed.zoneName || "the zone"}: ${observed.kpiValue ?? "—"}`,
        observed.alertEvent ? "complete" : "active"),
    ],
    complete: (observed) => Boolean(observed.alertEvent),
  },
  {
    id: "alert-reached",
    group: "explore",
    type: "explanation",
    route: "demo",
    dim: false,
    title: "Alert triggered",
    description: (observed) => observed.alertEvent?.count
      ? `${observed.alertEvent.count} ${observed.alertEvent.count === 1 ? "person was" : "people were"}`
        + ` in ${observed.zoneName || "the zone"} when the recorded alert fired.`
      : "The recorded threshold event is active.",
    actions: [
      { id: "explore", label: "Explore StoreLens", primary: true },
      { id: "exit", label: "Exit demo" },
    ],
  },
  {
    id: "explore",
    group: "explore",
    type: "terminal",
    dim: false,
    title: "Demo complete",
    description: "Everything on screen is the real StoreLens interface.",
  },
];

const STEPS_BY_ID = new Map(STEPS.map((step) => [step.id, step]));

/** Ordered steps for a branch. Unchosen-branch steps are excluded. */
export function planFor(branch) {
  return STEPS.filter((step) => !step.branch || step.branch === branch);
}

export function tourStepById(id) {
  return STEPS_BY_ID.get(id) || null;
}

export function stepText(value, observed) {
  return typeof value === "function" ? value(observed) : value;
}

export function initialTourState(sessionId, nowMs = 0) {
  return {
    sessionId,
    stepId: STEPS[0].id,
    branch: null,
    status: "loading",
    enteredAt: nowMs,
    completedAt: null,
    minimized: false,
    dismissed: false,
    error: null,
  };
}

function enter(state, stepId, nowMs) {
  const step = tourStepById(stepId);
  return {
    ...state,
    stepId,
    status: step?.type === "automatic" ? "loading"
      : step?.type === "terminal" ? "complete" : "waiting_for_user",
    enteredAt: nowMs,
    completedAt: null,
    error: null,
  };
}

export function advanceTour(state, nowMs = 0) {
  const plan = planFor(state.branch);
  const index = plan.findIndex((step) => step.id === state.stepId);
  const next = plan[index + 1];
  if (!next) return { ...state, status: "complete" };
  return enter(state, next.id, nowMs);
}

/** Record a branch choice; both branches converge on the same later steps. */
export function chooseTourBranch(state, branch, nowMs = 0) {
  if (branch !== "auto" && branch !== "manual") return state;
  const next = planFor(branch).find((step) => step.branch === branch);
  const withBranch = { ...state, branch };
  return next ? enter(withBranch, next.id, nowMs) : advanceTour(withBranch, nowMs);
}

/** A user-facing action press (Continue / Watch it run / branch choice). */
export function applyTourAction(state, actionId, nowMs = 0) {
  const step = tourStepById(state.stepId);
  const action = (step?.actions || []).find((item) => item.id === actionId);
  if (!action) return state;
  if (action.branch) return chooseTourBranch(state, action.branch, nowMs);
  return advanceTour(state, nowMs);
}

export function reportTourTargetMissing(state, message) {
  if (state.status === "error") return state;
  return { ...state, status: "error", error: message };
}

export function retryTourStep(state, nowMs = 0) {
  return enter(state, state.stepId, nowMs);
}

export function skipTourStep(state, nowMs = 0) {
  return advanceTour(state, nowMs);
}

/**
 * Move the machine forward from observed real state. Pure: same inputs, same
 * output. A required interaction is never advanced automatically (§39) — only
 * its own real completion condition, or an explicit skip, moves it on.
 */
export function evaluateTour(state, observed, nowMs = 0) {
  const step = tourStepById(state.stepId);
  if (!step || state.dismissed || step.type === "terminal") return state;
  const elapsed = Math.max(0, nowMs - state.enteredAt);
  if (state.status === "complete" && state.completedAt !== null) {
    return nowMs - state.completedAt >= COMPLETION_DWELL_MS ? advanceTour(state, nowMs) : state;
  }
  const done = step.complete ? Boolean(step.complete(observed, elapsed)) : false;
  if (done && elapsed >= (step.minMs || 0)) {
    return { ...state, status: "complete", completedAt: nowMs, error: null };
  }
  if (step.type === "automatic" && state.status !== "error") {
    const timeout = step.timeoutMs === undefined ? STEP_TIMEOUT_MS : step.timeoutMs;
    if (timeout && elapsed > timeout) {
      return { ...state, status: "error", error: `${stepText(step.title, observed)} did not complete.` };
    }
  }
  return state;
}

/** Checklist rows for the progress card. */
export function tourChecklist(state) {
  const plan = planFor(state.branch);
  const index = plan.findIndex((step) => step.id === state.stepId);
  const currentGroup = plan[index]?.group;
  const finished = state.status === "complete" && index === plan.length - 1;
  return TOUR_GROUPS.map((group) => {
    const steps = plan.filter((step) => step.group === group.id);
    const positions = steps.map((step) => plan.indexOf(step));
    if (group.id === currentGroup) {
      return { ...group, status: state.status === "error" ? "error" : state.status };
    }
    const passed = positions.length > 0 && positions.every((position) => position < index);
    return { ...group, status: passed || finished ? "complete" : "pending" };
  });
}

export function isChecklistGroup(id) {
  return CHECKLIST_IDS.has(id);
}

export function tourProgress(state) {
  const rows = tourChecklist(state);
  return {
    complete: rows.filter((row) => row.status === "complete").length,
    total: rows.length,
  };
}

/**
 * Everything the progress card shows, as plain data. Keeping the copy here (and
 * out of JSX) lets the whole walkthrough be asserted without a browser.
 */
export function tourCardView(state, observed, elapsedMs = 0) {
  const step = state ? tourStepById(state.stepId) : null;
  if (!step) return null;
  const failed = state.status === "error";
  return {
    stepId: step.id,
    group: step.group,
    type: step.type,
    status: state.status,
    title: stepText(step.title, observed),
    description: stepText(step.description, observed) || "",
    quote: step.quote || "",
    detail: step.detail ? step.detail(observed, elapsedMs) : [],
    hint: step.reopen?.(observed) ? "Reopen that dialog in StoreLens to continue." : "",
    actions: failed ? [] : step.actions || [],
    error: failed ? state.error : "",
    checklist: tourChecklist(state),
    progress: tourProgress(state),
    finished: step.type === "terminal",
    spotlightTarget: step.target || null,
    route: step.route || null,
    dims: step.dim !== false,
    blocksInteraction: step.type === "user_action" && Boolean(step.target),
  };
}

export function serializeTourState(state) {
  return JSON.stringify({
    version: 1,
    sessionId: state.sessionId,
    stepId: state.stepId,
    branch: state.branch,
    minimized: state.minimized,
    dismissed: state.dismissed,
  });
}

/**
 * Restore after a browser refresh. Only a payload belonging to the still-active
 * demo session is accepted; anything else starts the tour from the beginning.
 */
export function restoreTourState(raw, sessionId, nowMs = 0) {
  if (!raw || !sessionId) return null;
  let payload;
  try {
    payload = JSON.parse(raw);
  } catch {
    return null;
  }
  if (payload?.version !== 1 || payload.sessionId !== sessionId) return null;
  const step = tourStepById(payload.stepId);
  if (!step) return null;
  const branch = payload.branch === "auto" || payload.branch === "manual" ? payload.branch : null;
  if (step.branch && step.branch !== branch) return null;
  return {
    ...enter(initialTourState(sessionId, nowMs), step.id, nowMs),
    branch,
    minimized: Boolean(payload.minimized),
    dismissed: Boolean(payload.dismissed),
  };
}

const EMPTY_OBSERVED = {
  sessionId: null,
  workspaceReady: false,
  cameras: [],
  sourceCount: 0,
  placedCameraCount: 0,
  storeMapReady: false,
  replayReady: false,
  zoneId: null,
  zoneName: "",
  zoneCameras: [],
  queryId: null,
  queryName: "",
  alertRuleId: null,
  alertCondition: "",
  dashboardId: null,
  dashboardWidgetTitle: "",
  kpiValue: null,
  kpiQuality: "unknown",
  alertEvent: null,
  digitizerOpen: false,
  digitizer: { polygons: 0, scalePoints: 0, knownDistance: 0 },
  calibrationOpenFor: null,
  calibrationPairs: 0,
  planSavedAt: null,
  planRestoredAt: null,
  practiceCalibrationAt: null,
};

function cameraName(index) {
  return `Camera ${index}`;
}

/**
 * The occupancy StoreLens derived for the sample that produced a recorded alert.
 * This is read from the committed cache, never recomputed and never assumed: the
 * live KPI has usually moved on by the time the card reports the event.
 */
function qualifyingCount(cache, videoTime) {
  const timeline = cache?.timeline || [];
  let match = null;
  for (const sample of timeline) {
    if (Number(sample.video_time_s) <= Number(videoTime) + 1e-9) match = sample;
    else break;
  }
  return match?.kpi?.value ?? null;
}

/**
 * Build the observed snapshot the state machine consumes.
 *
 * `session` and `cache` are the real demo payloads, `workspace` holds real
 * demo-workspace reads (sources/zones/queries/alert rules/dashboards) and
 * `events` holds real UI completions reported by StoreLens controls.
 */
export function observeDemoState({ session, cache, replay, workspace = {}, events = {} } = {}) {
  if (!session) return { ...EMPTY_OBSERVED };
  const sourceIds = session.result?.source_ids || {};
  const cameraKeys = Object.keys(sourceIds);
  const sourcesById = new Map((workspace.sources || []).map((source) => [source.id, source]));
  const media = cache?.metadata?.media || {};
  const actionNames = (session.action_log || []).map((item) => item.name);
  const cameras = cameraKeys.map((key, index) => {
    const sourceId = sourceIds[key];
    const source = sourcesById.get(sourceId);
    return {
      index: index + 1,
      key,
      name: source?.name || cameraName(index + 1),
      sourceId,
      placed: Boolean(source?.placement),
      calibrated: Boolean(source?.calibrated),
      trackingFrames: Number(media[key]?.frame_count || 0),
    };
  });
  const zone = (workspace.zones || []).find((item) => item.id === session.result?.zone_id) || null;
  const query = (workspace.queries || []).find((item) => item.id === session.result?.query_id) || null;
  const rule = (workspace.rules || []).find((item) => item.id === session.result?.alert_rule_id) || null;
  const dashboard = (workspace.dashboards || []).find(
    (item) => item.id === session.result?.dashboard_id) || null;
  const alertEvent = replay?.alerts?.length ? replay.alerts.at(-1) : null;
  const zoneCameras = cameras.filter((camera) => (
    session.result?.camera_overlays?.[camera.key]?.zones || []).length > 0);
  return {
    ...EMPTY_OBSERVED,
    sessionId: session.id,
    workspaceReady: Boolean(session.id) && session.demo_workspace !== false,
    cameras,
    sourceCount: workspace.sources ? workspace.sources.length : cameraKeys.length,
    placedCameraCount: cameras.filter((camera) => camera.placed).length,
    storeMapReady: Boolean(workspace.store?.map?.floor_polygons?.length)
      || actionNames.includes("Create temporary mapped space"),
    replayReady: Boolean(cache?.timeline?.length),
    zoneId: session.result?.zone_id || null,
    zoneName: zone?.name || session.result?.zone_name || "",
    zoneCameras: zoneCameras.map((camera) => ({
      ...camera,
      viewAdded: (workspace.zoneViews || []).some((view) => view.source_id === camera.sourceId)
        || actionNames.some((name) => name.includes(`Camera ${camera.index}`) && name.includes("polygon")),
    })),
    queryId: query?.id || session.result?.query_id || null,
    queryName: query?.name || "",
    alertRuleId: rule?.id || session.result?.alert_rule_id || null,
    alertCondition: rule?.condition
      ? `${rule.condition.operator} ${rule.condition.value}` : "",
    dashboardId: dashboard?.id || session.result?.dashboard_id || null,
    dashboardWidgetTitle: dashboard?.widgets?.[0]?.title || "",
    kpiValue: replay?.kpi?.value ?? null,
    kpiQuality: replay?.kpi?.quality || "unknown",
    alertEvent: alertEvent
      ? { message: alertEvent.message || "",
          count: qualifyingCount(cache, alertEvent.video_time_s),
          videoTime: Number(alertEvent.video_time_s || 0) }
      : null,
    digitizerOpen: Boolean(events.digitizerOpen),
    digitizer: {
      polygons: Number(events.digitizer?.polygons || 0),
      scalePoints: Number(events.digitizer?.scalePoints || 0),
      knownDistance: Number(events.digitizer?.knownDistance || 0),
    },
    calibrationOpenFor: events.calibrationOpenFor ?? null,
    calibrationPairs: Number(events.calibrationPairs || 0),
    planSavedAt: events.planSavedAt || null,
    planRestoredAt: events.planRestoredAt || null,
    practiceCalibrationAt: events.practiceCalibrationAt || null,
  };
}
