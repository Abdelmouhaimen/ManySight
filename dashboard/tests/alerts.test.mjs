import assert from "node:assert/strict";
import test from "node:test";

import {
  RULE_KINDS,
  alertFacts,
  alertQuality,
  describeRule,
  formatSeconds,
  isRetiredRuleKind,
  operatorPhrase,
  ruleScope,
} from "../src/alerts.js";

const CONTEXT = {
  zones: [{ id: 4, name: "Aisle 04" }],
  sources: [{ id: 2, name: "Camera 2" }],
  queries: [{ id: 1, name: "Aisle 04 occupancy", question: "How many people are in Aisle 04?" }],
};

/* The operator words are the whole point: the platform never normalises
 * "more than 2" into "at least 2", so neither may the UI. */
test("each operator keeps its own phrase", () => {
  assert.equal(operatorPhrase(">"), "more than");
  assert.equal(operatorPhrase(">="), "at least");
  assert.equal(operatorPhrase("<"), "fewer than");
  assert.equal(operatorPhrase("<="), "at most");
  assert.equal(operatorPhrase("=="), "exactly");
  assert.notEqual(operatorPhrase(">"), operatorPhrase(">="));
});

test("an unknown operator is passed through, never guessed", () => {
  assert.equal(operatorPhrase("<=>"), "<=>");
  assert.equal(operatorPhrase(undefined), "");
});

test("a query rule reads as a sentence, not as parameters", () => {
  const rule = {
    kind: "query_condition",
    params: { query_id: 1 },
    condition: { operator: ">", value: 2 },
  };
  const sentence = describeRule(rule, CONTEXT);
  assert.equal(sentence, "How many people are in Aisle 04? is more than 2");
  assert.ok(!sentence.includes("{"), "must not leak raw JSON");
  assert.ok(!sentence.includes("query_id"));
});

test("a held condition says how long it must hold", () => {
  const rule = {
    kind: "query_condition",
    params: { query_id: 1 },
    condition: { operator: ">=", value: 2, for_seconds: 120 },
  };
  assert.equal(describeRule(rule, CONTEXT), "How many people are in Aisle 04? is at least 2 for 2 min");
});

test("zone and source ids become names", () => {
  assert.equal(
    describeRule({ kind: "dwell_exceeds", params: { zone_id: 4, seconds: 300 } }, CONTEXT),
    "Someone stays in Aisle 04 longer than 5 min",
  );
  assert.equal(
    describeRule({ kind: "state_alert", params: { name: "door", label: "open", source_id: 2 } }, CONTEXT),
    'door changes to "open" on Camera 2',
  );
});

test("a missing name falls back without crashing", () => {
  assert.equal(
    describeRule({ kind: "occupancy_exceeds", params: { count: 5, zone_id: 99 } }, CONTEXT),
    "More than 5 people in zone 99",
  );
  assert.equal(describeRule(null), "");
});

test("the scope line summarises where and how often", () => {
  const scope = ruleScope(
    { params: { zone_id: 4 }, cooldown_s: 60, condition: { allow_partial: true } },
    CONTEXT,
  );
  assert.equal(scope, "Aisle 04 · partial coverage accepted · at most once every 60s");
});

test("a fired alert becomes structured facts instead of an engine message", () => {
  const alert = {
    payload: {
      value: 3,
      condition: { operator: ">", value: 2 },
      zone_id: 4,
      quality: "known",
      held_since: 1786746642.64,
    },
  };
  const facts = Object.fromEntries(alertFacts(alert, CONTEXT));
  assert.equal(facts.Observed, "3");
  assert.equal(facts.Threshold, "more than 2");
  assert.equal(facts.Zone, "Aisle 04");
  assert.equal(alertQuality(alert), "known");
  // A raw Unix timestamp is not a fact a person can read.
  assert.ok(!/^\d{10}/.test(facts["True since"]), "must not print a Unix timestamp");
  assert.match(facts["True since"], /\d/);
});

test("facts that do not exist are not invented", () => {
  assert.deepEqual(alertFacts({ payload: {} }, CONTEXT), []);
  assert.equal(alertQuality({ payload: {} }), null);
});

/* A rule built on a kind the ingestion path now rejects could only ever match
 * historical rows, so it is not offered — but existing rules still render. */
test("retired rule kinds are not offered for new rules", () => {
  const offered = RULE_KINDS.map(([value]) => value);
  assert.ok(!offered.includes("event_match"));
  assert.ok(!offered.includes("analysis_condition"));
  assert.equal(isRetiredRuleKind("event_match"), true);
  assert.equal(isRetiredRuleKind("query_condition"), false);
  assert.ok(describeRule({ kind: "event_match", params: { event_type: "zone_enter" } }, CONTEXT).length);
});

test("durations read in human units", () => {
  assert.equal(formatSeconds(45), "45s");
  assert.equal(formatSeconds(300), "5 min");
  assert.equal(formatSeconds(7200), "2.0 hr");
  assert.equal(formatSeconds(undefined), "—");
});
