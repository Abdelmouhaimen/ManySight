/* Shared guided-tour fixtures shaped like the real demo payloads:
 * GET /demo/sessions/{id}, GET /demo/sessions/{id}/replay-cache and the
 * demo-scoped workspace reads the tour observes.
 */
import { evaluateTour, tourStepById } from "../src/demo-tour-model.js";

export const CAMERA_KEYS = [1, 2, 3, 4].map(
  (value) => `Warehouse_Synthetic_Cam${String(value).padStart(3, "0")}`,
);

export const SOURCE_IDS = Object.fromEntries(
  CAMERA_KEYS.map((key, index) => [key, index + 1]),
);

export function demoSession(overrides = {}) {
  return {
    id: "session-1",
    mode: "guided",
    status: "running",
    demo_workspace: true,
    duration_s: 20.066667,
    action_log: [
      { name: "Inspect workspace", status: "completed" },
      { name: "Create temporary mapped space", status: "completed" },
      ...CAMERA_KEYS.map((key, index) => ({
        name: `Import Camera ${index + 1} calibration`, status: "completed",
      })),
      { name: "Draw Aisle 04 polygon on Camera 3", status: "completed" },
      { name: "Draw Aisle 04 polygon on Camera 4", status: "completed" },
      { name: "Create canonical Aisle 04", status: "completed" },
    ],
    result: {
      source_ids: SOURCE_IDS,
      zone_id: 7,
      zone_name: "Aisle 04",
      group_id: 2,
      query_id: 5,
      dashboard_id: 3,
      alert_rule_id: 4,
      camera_overlays: Object.fromEntries(CAMERA_KEYS.map((key, index) => [key, {
        camera_key: key,
        source_id: SOURCE_IDS[key],
        zones: index >= 2 ? [{ name: "Aisle 04", polygons_px: [[[0, 0], [1, 0], [1, 1], [0, 1]]] }] : [],
      }])),
    },
    ...overrides,
  };
}

export function replayCache(overrides = {}) {
  return {
    metadata: {
      source_fps: 30,
      sample_rate_hz: 10,
      media: Object.fromEntries(CAMERA_KEYS.map((key) => [key, { frame_count: 602 }])),
    },
    timeline: [{ index: 0, video_time_s: 0, kpi: { value: 1, quality: "known" }, alert_events: [] }],
    ...overrides,
  };
}

export function demoWorkspace({ calibrated = true, placed = true, zoneViews = true } = {}) {
  return {
    store: { map: { floor_polygons: [[{ x: 0, y: 0 }]] }, width_m: 32.96, height_m: 31.5 },
    sources: CAMERA_KEYS.map((key, index) => ({
      id: SOURCE_IDS[key],
      name: `Camera ${index + 1}`,
      calibrated,
      placement: placed ? { x: 1, y: 2, rotation_deg: 0, fov_deg: 60 } : null,
      metadata: { demo_fixture_source_key: key },
    })),
    zones: [{ id: 7, name: "Aisle 04" }],
    zoneViews: zoneViews
      ? [{ id: 11, zone_id: 7, source_id: 3 }, { id: 12, zone_id: 7, source_id: 4 }]
      : [],
    queries: [{ id: 5, name: "People in Aisle 04", subject: "fused_entity" }],
    rules: [{ id: 4, name: "At least two fused people in Aisle 04", kind: "query_condition",
      condition: { operator: ">=", value: 2 } }],
    dashboards: [{ id: 3, name: "Aisle 04 live occupancy",
      widgets: [{ id: 9, query_id: 5, title: "Fused people in Aisle 04" }] }],
  };
}

export function replayState({ alerts = [], kpi = { value: 1, quality: "known" } } = {}) {
  return { kpi, alerts, entities: [], videoTime: 0, epoch: 0 };
}

/**
 * Advance the machine on a simulated clock. `observedFor(state, now)` returns the
 * observed snapshot, so a test can decide when a real completion appears.
 */
export function run(state, observedFor, { from = 0, stepMs = 100, maxMs = 120000 } = {}) {
  let now = from;
  let current = state;
  const visited = [current.stepId];
  while (now < from + maxMs) {
    now += stepMs;
    const next = evaluateTour(current, observedFor(current, now), now);
    if (next.stepId !== current.stepId) visited.push(next.stepId);
    current = next;
    const step = tourStepById(current.stepId);
    if (step?.type === "terminal") break;
    if (step?.type !== "automatic" && current.status === "waiting_for_user") break;
    if (current.status === "error") break;
  }
  return { state: current, now, visited };
}
