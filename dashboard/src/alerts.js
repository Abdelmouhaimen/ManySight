/* Human sentences for alert rules and fired alerts.
 *
 * Rule rows used to print `{"query_id":1}` and alert bodies used to print
 * `current_occupancy > 2 for 0s (query: People in Aisle 04)`. Both are accurate
 * and neither is a sentence. These helpers turn the stored definition into
 * something a person can read, and expose the same facts as structured rows so
 * a drawer can lay them out instead of quoting the engine.
 *
 * Pure: no React, no fetch. The raw payload always stays available behind
 * Technical details, so nothing here hides information — it only leads with the
 * readable form.
 */

/** "more than 2" etc. — the operator words the platform never normalises. */
export const OPERATOR_PHRASES = {
  ">": "more than",
  ">=": "at least",
  "<": "fewer than",
  "<=": "at most",
  "==": "exactly",
  "!=": "not",
};

export const operatorPhrase = (operator) => OPERATOR_PHRASES[operator] || operator || "";

const named = (collection, id, fallback) =>
  collection?.find((item) => Number(item.id) === Number(id))?.name || fallback;

/**
 * One sentence describing what a rule watches for.
 * `context` supplies zones, sources and saved queries so IDs become names.
 */
export function describeRule(rule, context = {}) {
  if (!rule) return "";
  const params = rule.params || {};
  const condition = rule.condition || {};
  const zone = params.zone_id != null
    ? named(context.zones, params.zone_id, `zone ${params.zone_id}`) : null;
  const source = params.source_id != null
    ? named(context.sources, params.source_id, `source ${params.source_id}`) : null;

  switch (rule.kind) {
    case "query_condition": {
      const query = context.queries?.find((item) => Number(item.id) === Number(params.query_id));
      const subject = query?.question || query?.name || "the saved result";
      const phrase = operatorPhrase(condition.operator);
      const held = Number(condition.for_seconds || 0) > 0
        ? ` for ${formatSeconds(condition.for_seconds)}` : "";
      if (condition.value == null || !phrase) return subject;
      return `${subject} is ${phrase} ${condition.value}${held}`;
    }
    case "occupancy_exceeds":
      return `More than ${params.count ?? "?"} people in ${zone || "the space"}`
        + (params.window_s ? ` within ${formatSeconds(params.window_s)}` : "");
    case "dwell_exceeds":
      return `Someone stays in ${zone || "any zone"} longer than ${formatSeconds(params.seconds)}`;
    case "state_alert":
      return params.min_seconds
        ? `${params.name || "State"} stays "${params.label}" for ${formatSeconds(params.min_seconds)}`
          + (source ? ` on ${source}` : "")
        : `${params.name || "State"} changes to "${params.label}"` + (source ? ` on ${source}` : "");
    case "event_match":
      return `A ${String(params.event_type || "matching").replaceAll("_", " ")} record arrives`
        + (zone ? ` in ${zone}` : "")
        + (params.attr_key ? ` with ${params.attr_key}=${params.attr_value ?? "any"}` : "");
    case "analysis_condition": {
      const measure = (rule.analysis?.measures || [])[0];
      return `${String(measure || "A measure").replaceAll("_", " ")} is `
        + `${operatorPhrase(condition.operator)} ${condition.value}`;
    }
    default:
      return String(rule.kind || "").replaceAll("_", " ");
  }
}

/** A short second line: where it watches and how often it may fire. */
export function ruleScope(rule, context = {}) {
  const parts = [];
  const params = rule?.params || {};
  if (params.zone_id != null) parts.push(named(context.zones, params.zone_id, "zone"));
  if (params.source_id != null) parts.push(named(context.sources, params.source_id, "source"));
  if (rule?.condition?.allow_partial) parts.push("partial coverage accepted");
  if (Number(rule?.cooldown_s || 0) > 0) {
    parts.push(`at most once every ${formatSeconds(rule.cooldown_s)}`);
  }
  if (rule?.webhook_url) parts.push("webhook");
  return parts.join(" · ");
}

/**
 * The structured facts a fired alert can show, in place of the engine's own
 * message string. Only facts that genuinely exist are returned.
 */
export function alertFacts(alert, context = {}) {
  const payload = alert?.payload || {};
  const rows = [];
  const observed = payload.value ?? payload.occupancy ?? payload.derived_dwell_s
    ?? payload.open_visit?.value ?? payload.derived_duration_s;
  if (observed != null) {
    // "Observed" here is the measured value. The drawer labels the alert's
    // timestamp "Observed at", so the two never collide.
    const label = payload.occupancy != null || payload.value != null ? "Observed" : "Duration";
    rows.push([label, payload.derived_dwell_s != null || payload.derived_duration_s != null
      ? formatSeconds(observed) : String(observed)]);
  }
  if (payload.condition?.operator && payload.condition?.value != null) {
    rows.push(["Threshold", `${operatorPhrase(payload.condition.operator)} ${payload.condition.value}`]);
  }
  const zoneId = payload.zone_id ?? payload.event?.zone_id ?? payload.open_visit?.zone_id
    ?? payload.query_result?.metadata?.zone_id;
  if (zoneId != null) rows.push(["Zone", named(context.zones, zoneId, `zone ${zoneId}`)]);
  const sourceId = payload.source_id ?? payload.event?.source_id;
  if (sourceId != null) rows.push(["Camera", named(context.sources, sourceId, `source ${sourceId}`)]);
  // The engine records this as a Unix timestamp; a person needs a clock time.
  if (payload.held_since) rows.push(["True since", formatClock(payload.held_since)]);
  return rows;
}

/** Local clock time for a Unix timestamp. Kept here so this module stays pure. */
export function formatClock(seconds) {
  const value = Number(seconds);
  if (!Number.isFinite(value) || value <= 0) return "—";
  return new Date(value * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

/** The quality of the evidence a query-backed alert fired on, when known. */
export function alertQuality(alert) {
  return alert?.payload?.quality || null;
}

export function formatSeconds(seconds) {
  const value = Number(seconds);
  if (!Number.isFinite(value)) return "—";
  if (value < 90) return `${Math.round(value)}s`;
  if (value < 5400) return `${Math.round(value / 60)} min`;
  return `${(value / 3600).toFixed(1)} hr`;
}

/**
 * Rule kinds offered when creating a rule.
 *
 * `event_match` is deliberately absent from the normal choices: every event
 * type it can match (`zone_enter`, `zone_exit`, `zone_dwell`, `state_change`,
 * `count`) is a kind the current ingestion path rejects, so a new rule built on
 * it could only ever match historical rows. Existing rules keep working and
 * still render; the option simply is not offered for new ones.
 */
export const RULE_KINDS = [
  ["query_condition", "Saved result crosses a threshold",
   "Watches a saved question, exactly as the dashboard shows it."],
  ["occupancy_exceeds", "Too many people in a zone",
   "Counts tracked people in one zone over a short window."],
  ["dwell_exceeds", "Someone stays too long",
   "Fires when a single tracked person exceeds a time limit in a zone."],
  ["state_alert", "Something stays in a state",
   "For door, light or machine states reported by a worker."],
];

export const RETIRED_RULE_KINDS = ["event_match", "analysis_condition"];

export const isRetiredRuleKind = (kind) => RETIRED_RULE_KINDS.includes(kind);
