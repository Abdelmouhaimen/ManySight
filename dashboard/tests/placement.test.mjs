/* Aiming a camera on the plan.
 *
 * Setup used to split this in two: the Space tab drew the view wedge but gave
 * you no way to change it, and the Cameras tab had the Direction and Field of
 * view sliders with no map beside them. Setting a direction meant moving a
 * slider, switching tabs to see the result, and coming back — aiming blind.
 *
 * Both halves now share the geometry below, so what these tests really pin is
 * that a click and the drawn wedge cannot drift apart.
 */
import test from "node:test";
import assert from "node:assert/strict";

import {
  DEFAULT_FOV_DEG, WEDGE_LENGTH_M, aimDegrees, fovWedge, normalizeDegrees, pointAlong,
  withinFieldOfView,
} from "../src/placement.js";

const CAMERA = { x: 5, y: 5, rotation_deg: 0, fov_deg: 70 };

/* Map metres run y-down, like the SVG. "Below" on screen is +y. */
test("aiming points at the clicked spot in map orientation", () => {
  assert.equal(aimDegrees(CAMERA, { x: 9, y: 5 }), 0);      // right
  assert.equal(aimDegrees(CAMERA, { x: 5, y: 9 }), 90);     // down the screen
  assert.equal(aimDegrees(CAMERA, { x: 1, y: 5 }), 180);    // left
  assert.equal(aimDegrees(CAMERA, { x: 5, y: 1 }), -90);    // up the screen
  assert.equal(aimDegrees(CAMERA, { x: 9, y: 9 }), 45);
});

test("a click on the camera itself does not spin it to an arbitrary angle", () => {
  assert.equal(aimDegrees(CAMERA, { x: 5, y: 5 }), null);
  assert.equal(aimDegrees(CAMERA, { x: 5.01, y: 4.99 }), null);
  assert.equal(aimDegrees(null, { x: 1, y: 1 }), null);
});

test("aiming stays in the range the direction slider accepts", () => {
  // The slider is min=-180 max=180, so a value outside that would be unreachable
  // by the control it is meant to agree with.
  for (let angle = 0; angle < 360; angle += 7) {
    const target = pointAlong(CAMERA, angle, 3);
    const aimed = aimDegrees(CAMERA, target);
    assert.ok(aimed > -180 && aimed <= 180, `${angle}° produced ${aimed}`);
  }
});

test("normalising wraps rather than clamping", () => {
  assert.equal(normalizeDegrees(190), -170);
  assert.equal(normalizeDegrees(-190), 170);
  assert.equal(normalizeDegrees(180), 180);
  assert.equal(normalizeDegrees(-180), 180);
  assert.equal(normalizeDegrees(720 + 45), 45);
});

/* The invariant the whole feature rests on: what you click is what you see. */
test("aiming at a spot swings the drawn wedge over that spot", () => {
  for (const target of [{ x: 9, y: 5 }, { x: 2, y: 8 }, { x: 5, y: 0 }, { x: 0.5, y: 9.5 }]) {
    const aimed = { ...CAMERA, rotation_deg: aimDegrees(CAMERA, target) };
    assert.ok(withinFieldOfView(aimed, target),
              `clicked ${JSON.stringify(target)} but the wedge does not cover it`);
  }
});

test("the wedge is drawn around the direction, not beside it", () => {
  const [apex, left, right] = fovWedge({ x: 5, y: 5, rotation_deg: 0, fov_deg: 90 });
  assert.deepEqual(apex, { x: 5, y: 5 }, "the apex is the camera itself");
  // 90° FOV centred on 0° spans -45°..45°, so both edges sit to the right.
  assert.ok(left.x > 5 && right.x > 5);
  assert.ok(left.y < 5 && right.y > 5, "one edge each side of the centreline");
  assert.equal(Math.round(Math.hypot(left.x - 5, left.y - 5) * 100) / 100, WEDGE_LENGTH_M);
});

test("a narrow field of view covers less than a wide one", () => {
  const offAxis = { x: 8, y: 6.5 };                 // ~26.6° off the centreline
  assert.ok(withinFieldOfView({ ...CAMERA, fov_deg: 70 }, offAxis));
  assert.ok(!withinFieldOfView({ ...CAMERA, fov_deg: 30 }, offAxis));
});

test("an unspecified placement falls back to the documented defaults", () => {
  const [, left, right] = fovWedge({ x: 0, y: 0 });
  const spread = Math.abs(aimDegrees({ x: 0, y: 0 }, left) - aimDegrees({ x: 0, y: 0 }, right));
  assert.equal(spread, DEFAULT_FOV_DEG);
});
