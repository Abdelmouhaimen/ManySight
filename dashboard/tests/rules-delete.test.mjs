import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

const source = readFileSync(new URL("../src/review.jsx", import.meta.url), "utf8");
const rulesView = source.slice(
  source.indexOf("function RulesView"),
  source.indexOf("function RuleModal"),
);

test("rule deletion uses an in-app confirmation instead of window.confirm", () => {
  assert.doesNotMatch(rulesView, /window\.confirm/);
  assert.match(rulesView, /onSelect: \(\) => setDeleting\(rule\)/);
  assert.match(rulesView, /title="Delete alert rule\?"/);
});

test("the confirmed action calls the alert-rule DELETE endpoint and reloads", () => {
  assert.match(rulesView, /api\.del\(`\/alert-rules\/\$\{rule\.id\}`\)/);
  assert.match(rulesView, /await load\(\)/);
  assert.match(rulesView, /Delete rule/);
});
