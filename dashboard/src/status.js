/* One presentation vocabulary for every state ManySight shows.
 *
 * The platform's enums are deliberately precise and deliberately different from
 * each other: a source's ingestion age, a worker's heartbeat age and a fused
 * result's evidence coverage are three unrelated facts. The UI used to surface
 * all three with overlapping words ("active", "recent", "paused", "stale",
 * "unreported"), so the same camera could read Live, Recent and paused on three
 * screens at once.
 *
 * These mappers translate API values into the small vocabulary users see. They
 * are pure so the mapping is tested once instead of re-derived per page, and
 * they never invent a state: anything unrecognised falls back to a neutral
 * "Unknown" rather than an optimistic default.
 */

/** tone drives icon + text; never colour alone. */
export const TONE = {
  good: "good",
  warn: "warn",
  bad: "bad",
  idle: "idle",
  info: "info",
};

const state = (label, tone, help = "") => ({ label, tone, help });

/* ---------------------------------------------------------------- sources */

/** Is this source's data arriving? Derived from ingestion age only. */
export function dataHealth(source) {
  switch (source?.observation_status) {
    case "active":
      return state("Live", TONE.good, "Data arrived in the last 30 seconds.");
    case "recent":
      return state("Live", TONE.good, "Data arrived in the last few minutes.");
    case "stale":
      return state("Stale", TONE.warn, "No data has arrived recently.");
    default:
      return state("No data", TONE.idle, "This source has never sent data.");
  }
}

/** Is this source ready to contribute geometry? */
export function setupStatus(source) {
  if (source?.calibrated && source?.placement) return state("Ready", TONE.good);
  if (!source?.placement) return state("Needs setup", TONE.warn, "Not placed on the floor map.");
  return state("Needs setup", TONE.warn, "Placed, but not calibrated.");
}

export function placementStatus(source) {
  return source?.placement ? state("Ready", TONE.good) : state("Needs setup", TONE.warn);
}

export function calibrationStatus(source) {
  return source?.calibrated ? state("Ready", TONE.good) : state("Needs setup", TONE.warn);
}

/* ------------------------------------------------------------ perception */

/** Worker runtime. `null`/absent means nothing is registered, which is not an error. */
export function runtimeStatus(worker) {
  if (!worker) return state("—", TONE.idle, "No worker is registered for this source.");
  switch (worker.effective_status) {
    case "running":
      return state("Running", TONE.good);
    case "starting":
    case "stopping":
      return state("Running", TONE.good, `Worker reported "${worker.effective_status}".`);
    case "error":
      return state("Error", TONE.bad, worker.last_error || "The worker reported an error.");
    case "stale":
      return state("Error", TONE.bad, "The worker has missed its heartbeats.");
    case "stopped":
      return state("Stopped", TONE.idle);
    default:
      return state("Stopped", TONE.idle);
  }
}

/* --------------------------------------------------------------- quality */

/**
 * Result confidence. This is the platform's most important honesty guarantee:
 * an unknown result is NOT a known zero, so `hasValue` is false for unknown and
 * callers must render a dash instead of the numeric payload.
 */
export function resultQuality(quality) {
  switch (quality) {
    case "known":
      return { ...state("Known", TONE.good), hasValue: true };
    case "partial":
      return {
        ...state("Partial coverage", TONE.warn,
          "Some contributing cameras are not reporting, so this may be an undercount."),
        hasValue: true,
      };
    default:
      return {
        ...state("Unknown", TONE.idle,
          "No usable evidence right now. This is not a zero."),
        hasValue: false,
      };
  }
}

/** The value a result card should print, honouring unknown-is-not-zero. */
export function resultValue(value, quality) {
  const presentation = resultQuality(quality);
  if (!presentation.hasValue || value == null) return "—";
  return value;
}

/* ---------------------------------------------------------------- alerts */

export function ruleStatus(rule) {
  return rule?.enabled ? state("Enabled", TONE.good) : state("Paused", TONE.idle);
}

export function alertStatus(alert) {
  const value = alert?.status || (alert?.acknowledged ? "resolved" : "new");
  switch (value) {
    case "in_review":
      return { ...state("In review", TONE.info), value };
    case "resolved":
      return { ...state("Resolved", TONE.good), value };
    case "dismissed":
      return { ...state("Dismissed", TONE.idle), value };
    default:
      return { ...state("New", TONE.warn), value: "new" };
  }
}

export const ALERT_REVIEW_STATES = [
  ["new", "New"],
  ["in_review", "In review"],
  ["resolved", "Resolved"],
  ["dismissed", "Dismissed"],
];

export const isOpenAlert = (alert) => ["new", "in_review"].includes(alertStatus(alert).value);

/* ------------------------------------------------------- combined tracking */

/**
 * Readiness of one multiview group, expressed the way a person asks it:
 * are these cameras set up to work together, and are they all reporting?
 */
export function combinedTrackingStatus(group, sources = []) {
  if (!group) return state("Not configured", TONE.idle,
    "Cameras that overlap are counted separately until they are combined.");
  const members = sources.filter((source) => (group.source_ids || []).includes(source.id));
  const uncalibrated = members.filter((source) => !source.calibrated);
  if (!group.enabled) return state("Paused", TONE.idle);
  if (uncalibrated.length) {
    return state("Needs setup", TONE.warn,
      `${countLabel(uncalibrated.length, "camera")} still `
      + `${uncalibrated.length === 1 ? "needs" : "need"} calibration.`);
  }
  const quiet = members.filter((source) => dataHealth(source).label !== "Live");
  if (quiet.length) {
    return state("Partial", TONE.warn,
      `${quiet.length} of ${members.length} cameras are not sending data.`);
  }
  return state("Ready", TONE.good);
}

/* ---------------------------------------------------- observation kinds */

/** Kinds a worker may submit today. */
export const CURRENT_KINDS = ["detection", "measurement", "state"];
/**
 * Retired kinds. `POST /observations/batch` rejects them, so they can only
 * appear on historical rows — they stay readable but are not offered up front.
 */
export const LEGACY_KINDS = ["zone_enter", "zone_exit", "zone_dwell", "state_change", "count"];
/** Free-form kinds that are still accepted. */
export const OTHER_KINDS = ["transition", "custom"];

export const isLegacyKind = (kind) => LEGACY_KINDS.includes(kind);

export function kindLabel(kind) {
  if (!kind) return "—";
  return kind.charAt(0).toUpperCase() + kind.slice(1).replaceAll("_", " ");
}

/* -------------------------------------------------------------- counting */

/**
 * Observation counts are only incremented for submissions that carry a job_id,
 * so a healthy worker posting detection samples without one reads as zero. A
 * count we cannot trust is shown as "—" rather than as a confident zero.
 */
export function trustworthyCount(count, { hasRuntime = false } = {}) {
  const value = Number(count || 0);
  if (value > 0) return value.toLocaleString();
  return hasRuntime ? "—" : "0";
}

/** "3 of 4 cameras" — used wherever a partial set is summarised. */
export function countLabel(count, singular, plural = `${singular}s`) {
  return `${count} ${count === 1 ? singular : plural}`;
}
