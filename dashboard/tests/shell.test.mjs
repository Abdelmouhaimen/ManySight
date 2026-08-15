/* §89 — the global shell.
 *
 * The sidebar order and the shell's copy are product decisions the audit found
 * wrong before (two demo entries, a socket status dressed up as data health, a
 * decorative environment badge). They are asserted here: the nav order is pure
 * data, and the rest is checked against the shell source, which is the only way
 * to pin shell copy without a browser in this environment.
 */
import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

import { NAV_ITEMS, ROUTES, routeHref } from "../src/routes.js";

const shell = readFileSync(new URL("../src/main.jsx", import.meta.url), "utf8");

test("the sidebar is the six product areas, in order", () => {
  assert.deepEqual(NAV_ITEMS.map(([value]) => value), [
    "dashboard", "live", "review", "observations", "sources", "setup",
  ]);
  assert.deepEqual(NAV_ITEMS.map(([, label]) => label), [
    "Dashboard", "Live", "Review", "Observations", "Sources", "Setup",
  ]);
});

test("the guided demo is not a sidebar entry", () => {
  assert.ok(!NAV_ITEMS.some(([value]) => value === "demo"));
  assert.ok(!NAV_ITEMS.some(([, label]) => /demo/i.test(label)));
});

test("demo is still a real route, reachable from Try Demo in the top bar", () => {
  assert.ok(ROUTES.includes("demo"));
  assert.match(shell, /data-demo-tour="try-demo"/);
  assert.match(shell, /Try Demo/);
  assert.match(shell, /href=\{routeHref\("demo"\)\}/);
});

test("every sidebar entry links to its canonical route", () => {
  for (const [value] of NAV_ITEMS) {
    assert.ok(ROUTES.includes(value), `${value} must be a real route`);
    assert.equal(routeHref(value), `#${value}`);
  }
});

test("the review badge counts only alerts that are still open", () => {
  assert.match(shell, /value === "review" && openAlertCount > 0/);
  assert.match(shell, /shell\.alerts\.filter\(isOpenAlert\)/);
});

test("the top bar shows the workspace name and no operations tagline", () => {
  assert.match(shell, /className="workspace-name"/);
  assert.ok(!shell.includes("SPACE_LABELS"), "the '{type} operations · StoreLens' line is gone");
  assert.ok(!/operations/i.test(shell));
});

/* The old indicator said "Observation updates live" whenever the SSE socket was
 * open — a transport fact wearing the clothes of data health. */
test("stream connectivity is never presented as observation health", () => {
  assert.ok(!shell.includes("Observation updates live"));
  assert.ok(!shell.includes("Observation updates offline"));
  assert.match(shell, /Reconnecting…/);
  assert.match(shell, /this reports the update channel, not data health/);
});

test("the decorative environment badge is gone from the shell", () => {
  assert.ok(!shell.includes("EnvironmentBadge"));
  assert.ok(!/Setup incomplete|Example data|Live pilot/.test(shell));
});

test("the permanent sidebar note and demo paragraph are gone", () => {
  assert.ok(!shell.includes("sidebar-note"));
  assert.ok(!/Validate model output/.test(shell));
  assert.ok(!/Normal workspace data is untouched/.test(shell));
});

test("the source counter reports live sources, not merely registered ones", () => {
  assert.match(shell, /dataHealth\(source\)\.label === "Live"/);
});
