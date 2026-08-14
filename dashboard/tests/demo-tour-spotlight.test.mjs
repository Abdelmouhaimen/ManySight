import test from "node:test";
import assert from "node:assert/strict";

import {
  CARD_MARGIN, MAX_TARGET_ATTEMPTS, blockerRects, cardPlacement, isRectVisible, maskPath,
  resolveTourTarget, rectsOverlap, spotlightGeometry, targetAttemptDelay,
  targetAttemptsExhausted, tourTargetSelector,
} from "../src/demo-tour-spotlight.js";

const VIEWPORT = { width: 1440, height: 900 };

function fakeRoot(hooks) {
  const queried = [];
  return {
    queried,
    querySelector(selector) {
      queried.push(selector);
      const match = Object.keys(hooks).find((name) => selector === tourTargetSelector(name));
      return match ? hooks[match] : null;
    },
  };
}

function areaOf(rect) {
  return Math.max(0, rect.width) * Math.max(0, rect.height);
}

test("targets resolve through stable hooks, never structural selectors", () => {
  const button = { id: "digitize" };
  const root = fakeRoot({ "digitize-plan": button });
  assert.equal(resolveTourTarget("digitize-plan", root), button);
  assert.equal(root.queried[0], '[data-demo-tour="digitize-plan"]');
  assert.doesNotMatch(root.queried[0], /nth-child|>|\.panel/);
  assert.equal(resolveTourTarget("camera-calibrate-1", root), null);
  assert.equal(resolveTourTarget("", root), null);
  assert.equal(resolveTourTarget("digitize-plan", null), null);
});

test("an unavailable target is retried with backoff before giving up", () => {
  assert.ok(targetAttemptDelay(0) < targetAttemptDelay(3));
  assert.ok(targetAttemptDelay(50) <= 400);
  assert.equal(targetAttemptsExhausted(1), false);
  assert.equal(targetAttemptsExhausted(MAX_TARGET_ATTEMPTS), true);
});

test("the spotlight hole pads the target and stays inside the viewport", () => {
  const hole = spotlightGeometry({ top: 300, left: 200, width: 180, height: 44 },
    { viewport: VIEWPORT, padding: 10 });
  assert.deepEqual(
    { top: hole.top, left: hole.left, width: hole.width, height: hole.height },
    { top: 290, left: 190, width: 200, height: 64 },
  );
  assert.equal(hole.offscreen, false);
  const clamped = spotlightGeometry({ top: -20, left: -30, width: 200, height: 60 },
    { viewport: VIEWPORT });
  assert.equal(clamped.top, 0);
  assert.equal(clamped.left, 0);
  assert.ok(clamped.width > 0 && clamped.height > 0);
  const tiny = spotlightGeometry({ top: 10, left: 10, width: 8, height: 8 },
    { viewport: VIEWPORT, padding: 2 });
  assert.ok(tiny.radius <= Math.min(tiny.width, tiny.height) / 2,
    "the corner radius never exceeds half the hole");
});

test("a missing or off-screen target degrades safely instead of drawing a broken hole", () => {
  assert.equal(spotlightGeometry(null, { viewport: VIEWPORT }), null);
  assert.equal(spotlightGeometry({ top: 0, left: 0, width: 0, height: 0 }, { viewport: VIEWPORT }), null);
  assert.equal(spotlightGeometry({ top: 10, left: 10, width: 100, height: 20 }, {}), null);
  const scrolledAway = spotlightGeometry({ top: 2000, left: 100, width: 200, height: 40 },
    { viewport: VIEWPORT });
  assert.equal(scrolledAway.offscreen, true);
  assert.equal(isRectVisible({ top: 2000, left: 0, width: 10, height: 10 }, VIEWPORT), false);
  assert.equal(isRectVisible({ top: 10, left: 0, width: 10, height: 10 }, VIEWPORT), true);
});

test("click blockers surround the hole and leave the real control interactive", () => {
  const hole = spotlightGeometry({ top: 300, left: 200, width: 200, height: 60 },
    { viewport: VIEWPORT });
  const blockers = blockerRects(hole, VIEWPORT);
  assert.equal(blockers.length, 4);
  for (const rect of blockers) {
    assert.equal(rectsOverlap(rect, hole), false, "no blocker may cover the target");
  }
  const covered = blockers.reduce((total, rect) => total + areaOf(rect), 0);
  assert.equal(covered, VIEWPORT.width * VIEWPORT.height - areaOf(hole));
});

test("without a resolvable hole the blocker covers the page as one rectangle", () => {
  assert.deepEqual(blockerRects(null, VIEWPORT),
    [{ top: 0, left: 0, width: 1440, height: 900 }]);
  const offscreen = { top: 900, left: 0, width: 0, height: 0, offscreen: true };
  assert.equal(blockerRects(offscreen, VIEWPORT).length, 1);
  assert.deepEqual(blockerRects(null, null), []);
});

test("edge holes drop degenerate blockers instead of rendering zero-size strips", () => {
  const hole = spotlightGeometry({ top: 0, left: 0, width: 300, height: 80 },
    { viewport: VIEWPORT, padding: 0 });
  const blockers = blockerRects(hole, VIEWPORT);
  assert.ok(blockers.length < 4);
  assert.ok(blockers.every((rect) => areaOf(rect) > 0));
});

test("the progress card keeps its documented position until it would cover the target", () => {
  const card = { width: 340, height: 320 };
  const clear = cardPlacement({ card, hole: null, viewport: VIEWPORT });
  assert.equal(clear.placement, "top-left");
  assert.equal(clear.left, CARD_MARGIN);
  const overTopLeft = spotlightGeometry({ top: 120, left: 40, width: 320, height: 200 },
    { viewport: VIEWPORT });
  const moved = cardPlacement({ card, hole: overTopLeft, viewport: VIEWPORT });
  assert.notEqual(moved.placement, "top-left");
  assert.equal(rectsOverlap(moved, overTopLeft, CARD_MARGIN), false);
});

test("when every corner is covered the card takes the least-overlapping one", () => {
  const card = { width: 340, height: 320 };
  const wholePage = spotlightGeometry({ top: 0, left: 0, width: 1440, height: 880 },
    { viewport: VIEWPORT });
  const placement = cardPlacement({ card, hole: wholePage, viewport: VIEWPORT });
  assert.ok(placement, "a placement is always returned");
  assert.ok(placement.top >= 0 && placement.left >= 0);
  assert.equal(cardPlacement({ card: null, viewport: VIEWPORT }), null);
});

test("the card avoids every obstacle it is given, not just the spotlight", () => {
  const card = { width: 300, height: 220 };
  const dialog = { top: 200, left: 500, width: 500, height: 400 };
  const hole = spotlightGeometry({ top: 120, left: 40, width: 200, height: 120 },
    { viewport: VIEWPORT });
  const placement = cardPlacement({ card, obstacles: [hole, dialog], viewport: VIEWPORT });
  assert.equal(rectsOverlap(placement, hole, CARD_MARGIN), false);
  assert.equal(rectsOverlap(placement, dialog, CARD_MARGIN), false);
  assert.notEqual(placement.placement, "top-left", "the preferred corner was taken");

  // When nothing is free, the least-intrusive corner wins rather than nothing.
  const everywhere = { top: 0, left: 0, width: VIEWPORT.width, height: 840 };
  const crowded = cardPlacement({ card, obstacles: [everywhere], viewport: VIEWPORT });
  assert.ok(crowded && crowded.top >= 0);
});

test("an inset keeps the card clear of persistent chrome such as the sidebar", () => {
  const card = { width: 300, height: 220 };
  const sidebarRight = 210;
  const placement = cardPlacement({
    card, obstacles: [], viewport: VIEWPORT,
    inset: { top: 84, left: sidebarRight + CARD_MARGIN, right: CARD_MARGIN, bottom: CARD_MARGIN },
  });
  assert.equal(placement.placement, "top-left");
  assert.ok(placement.left >= sidebarRight, `${placement.left} must clear ${sidebarRight}`);
  assert.equal(placement.top, 84);
});

test("a narrow viewport shrinks the card box instead of pushing it off screen", () => {
  const narrow = { width: 420, height: 640 };
  const placement = cardPlacement({ card: { width: 340, height: 700 }, hole: null, viewport: narrow });
  assert.ok(placement.left + placement.width <= narrow.width);
  assert.ok(placement.top + placement.height <= narrow.height);
});

test("the mask cuts exactly one rounded hole out of the dimmed page", () => {
  const hole = spotlightGeometry({ top: 100, left: 100, width: 200, height: 80 },
    { viewport: VIEWPORT });
  const path = maskPath(hole, VIEWPORT);
  assert.match(path, /^M0 0H1440V900H0Z/);
  assert.equal((path.match(/M/g) || []).length, 2, "outer rectangle plus one hole");
  assert.equal(maskPath(null, VIEWPORT), "M0 0H1440V900H0Z");
  assert.equal(maskPath(hole, null), "");
});

test("resize and scroll only change geometry, never which target is spotlighted", () => {
  const target = { top: 500, left: 90, width: 220, height: 48 };
  const before = spotlightGeometry(target, { viewport: VIEWPORT });
  const afterScroll = spotlightGeometry({ ...target, top: 120 }, { viewport: VIEWPORT });
  const afterResize = spotlightGeometry(target, { viewport: { width: 900, height: 600 } });
  assert.notDeepEqual(before, afterScroll);
  assert.equal(afterScroll.width, before.width);
  assert.equal(afterResize.left, before.left);
  assert.ok(afterResize.height > 0);
});
