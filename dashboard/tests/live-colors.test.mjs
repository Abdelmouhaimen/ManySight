import assert from "node:assert/strict";
import test from "node:test";
import * as THREE from "three";

import { trackColor } from "../src/live-colors.js";

test("global track colors are stable, distinct, and Three.js-safe", () => {
  const ids = ["F0123456789abcdef", "F1111111111111111", "F2222222222222222"];
  const colors = ids.map(trackColor);

  assert.equal(trackColor(ids[0]), colors[0]);
  assert.equal(new Set(colors).size, ids.length);
  colors.forEach((color) => {
    assert.match(color, /^#[0-9a-f]{6}$/);
    assert.equal(`#${new THREE.Color(color).getHexString()}`, color);
  });
});
