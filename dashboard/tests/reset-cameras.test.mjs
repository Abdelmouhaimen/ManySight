/* Reset cameras — the Setup → Advanced → Danger zone action.
 *
 * There is no browser in this environment, so the contract is pinned against
 * the source: that the card exists in the danger zone, that the destructive
 * call is gated behind a server dry run and a typed confirmation, that the copy
 * says what survives, and that success refreshes state instead of reloading the
 * page. `copy-audit.test.mjs` separately forbids internal jargon in this file's
 * user-visible strings.
 */
import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

const setup = readFileSync(new URL("../src/setup.jsx", import.meta.url), "utf8");

const block = setup.slice(
  setup.indexOf("function ResetCamerasBlock"),
  setup.indexOf("function DangerZone"),
);

test("the card lives in the danger zone next to the other resets", () => {
  assert.ok(block.length > 0, "ResetCamerasBlock must exist");
  const dangerZone = setup.slice(setup.indexOf("function DangerZone"));
  assert.match(dangerZone, /<ResetCamerasBlock/);
  assert.match(dangerZone, /Start the space again/);
  assert.match(dangerZone, /Clear all observations/);
  // Advanced is where it belongs; no new top-level navigation.
  assert.match(setup, /const TABS = \[\["space", "Space"\], \["cameras", "Cameras"\], \["advanced", "Advanced"\]\]/);
});

test("the card says what it does in the user's terms", () => {
  assert.match(block, /<h3>Reset cameras<\/h3>/);
  assert.match(block, /Remove all cameras and camera-specific setup/);
  assert.match(block, /configure cameras again from\s+scratch/);
});

test("nothing is removed before a server-side preview", () => {
  // The button opens a dry run; only the modal can execute.
  assert.match(block, /api\.post\("\/workspace\/reset-cameras", \{ dry_run: true \}\)/);
  assert.match(block, /onClick=\{openPreview\}/);
  const executeIndex = block.indexOf("dry_run: false");
  assert.ok(executeIndex > block.indexOf("dry_run: true"),
    "the preview must come first in the flow");
});

test("executing needs the typed confirmation and carries the preview token", () => {
  assert.match(block, /confirmation !== "RESET CAMERAS"/);
  assert.match(block, /Type RESET CAMERAS to confirm/);
  assert.match(block, /reset_token: impact\.reset_token/);
  // A preview reporting zero cameras cannot be executed.
  assert.match(block, /\|\| !impact\.cameras/);
});

test("the modal explains what is removed and what is kept", () => {
  assert.match(block, /This removes/);
  assert.match(block, /This keeps/);
  for (const removed of [/Cameras/, /Saved connections/, /Calibrations/,
                         /Camera views of zones/, /Camera observations/,
                         /Combined tracking groups/]) {
    assert.match(block, removed);
  }
  assert.match(block, /floor plan/);
  assert.match(block, /lose their\s+camera views/);
  assert.match(block, /turned\s+off because it relies on these cameras/);
  assert.match(block, /This cannot be undone/);
});

test("success notifies and refreshes rather than reloading the page", () => {
  assert.match(block, /notify\?\.\(/);
  assert.match(block, /await onReset\?\.\(\)/);
  assert.ok(!/window\.location\.reload/.test(block),
    "a reset must not force a full page reload");
});

test("the action is unavailable inside the guided demo", () => {
  assert.match(block, /demoSessionId\(\)/);
  assert.match(block, /disabled=\{busy \|\| inDemo\}/);
  assert.match(block, /className="muted">Exit the demo before resetting your cameras/);
});
