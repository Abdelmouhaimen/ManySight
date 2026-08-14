import test from "node:test";
import assert from "node:assert/strict";

import { DEFAULT_ROUTE, ROUTES, ROUTE_ALIASES, resolveRoute, routeHref } from "../src/routes.js";

test("every canonical route resolves to itself without a redirect", () => {
  for (const route of ROUTES) {
    const resolved = resolveRoute(`#${route}`);
    assert.equal(resolved.route, route);
    // Pages with sub-views canonicalise to their first sub-view.
    if (!resolved.subview) {
      assert.equal(resolved.canonical, route);
      assert.equal(resolved.redirected, false, `${route} should not redirect`);
    }
  }
});

test("legacy hashes still work and are rewritten to the canonical route", () => {
  for (const [alias, target] of Object.entries(ROUTE_ALIASES)) {
    const resolved = resolveRoute(`#${alias}`);
    assert.equal(resolved.route, target, `${alias} → ${target}`);
    assert.equal(resolved.redirected, true, `${alias} must heal the address bar`);
  }
  // The specific aliases the audit found in the wild.
  assert.equal(resolveRoute("#overview").canonical, "dashboard");
  assert.equal(resolveRoute("#detections").canonical, "observations");
  assert.equal(resolveRoute("#configure").canonical, "setup/space");
  assert.equal(resolveRoute("#events").canonical, "review/alerts");
});

test("an unknown hash normalises to the dashboard instead of silently rendering it", () => {
  const resolved = resolveRoute("#nonsense");
  assert.equal(resolved.route, DEFAULT_ROUTE);
  assert.equal(resolved.canonical, "dashboard");
  assert.equal(resolved.redirected, true, "the address bar must be corrected");
  assert.equal(resolveRoute("").canonical, "dashboard");
  assert.equal(resolveRoute("#").canonical, "dashboard");
  assert.equal(resolveRoute(undefined).canonical, "dashboard");
});

test("sub-views are addressable and default to the first one", () => {
  assert.deepEqual(
    ["review", "review/alerts", "review/rules"].map((hash) => resolveRoute(hash).subview),
    ["alerts", "alerts", "rules"],
  );
  assert.deepEqual(
    ["setup", "setup/space", "setup/cameras", "setup/advanced"]
      .map((hash) => resolveRoute(hash).subview),
    ["space", "space", "cameras", "advanced"],
  );
  // A bad sub-view falls back to the first rather than rendering nothing.
  assert.equal(resolveRoute("#setup/banana").subview, "space");
  assert.equal(resolveRoute("#setup/banana").redirected, true);
  // Pages without sub-views ignore a trailing segment.
  assert.equal(resolveRoute("#live/anything").subview, null);
});

test("routeHref builds the canonical link form", () => {
  assert.equal(routeHref("dashboard"), "#dashboard");
  assert.equal(routeHref("review", "rules"), "#review/rules");
  assert.equal(routeHref("setup", "cameras"), "#setup/cameras");
});

test("route resolution is case- and slash-tolerant", () => {
  assert.equal(resolveRoute("#Dashboard").route, "dashboard");
  assert.equal(resolveRoute("#/sources/").route, "sources");
  assert.equal(resolveRoute("#Setup/Cameras").subview, "cameras");
});
