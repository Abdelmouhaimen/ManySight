/* Where a camera sits on the plan and which way it looks.
 *
 * One convention, in one place, because two of them silently disagreeing is how
 * a camera ends up drawn facing one way and aimed another. Map metres run
 * y-down, exactly like the SVG they are drawn in, so a plain screen-space
 * atan2/cos/sin pair is correct here and no axis needs flipping.
 */

/** Half-length of the drawn view wedge, in map metres. Presentation, not data. */
export const WEDGE_LENGTH_M = 2.1;
export const DEFAULT_FOV_DEG = 70;

const RADIANS = Math.PI / 180;

/** Normalise to (-180, 180] so the slider and a click agree on a value. */
export function normalizeDegrees(degrees) {
  const wrapped = ((degrees % 360) + 360) % 360;
  return wrapped > 180 ? wrapped - 360 : wrapped;
}

/** The point `distance` metres from `origin` along `degrees`. */
export function pointAlong(origin, degrees, distance) {
  return {
    x: origin.x + Math.cos(degrees * RADIANS) * distance,
    y: origin.y + Math.sin(degrees * RADIANS) * distance,
  };
}

/**
 * The direction a camera at `from` must face to look at `to`.
 *
 * Returns null when the target is the camera itself: "which way" has no answer
 * there, and guessing one would spin the wedge on a stray click.
 */
export function aimDegrees(from, to) {
  if (!from || !to) return null;
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  if (Math.hypot(dx, dy) < 0.05) return null;
  return normalizeDegrees(Math.round((Math.atan2(dy, dx) * 180) / Math.PI));
}

/**
 * The view wedge for a placement: apex at the camera, one edge per FOV limit.
 *
 * The glyph and `aimDegrees` share this so that clicking a spot on the map
 * really does swing the drawn wedge over that spot.
 */
export function fovWedge(placement, length = WEDGE_LENGTH_M) {
  const { x, y, rotation_deg: rotation = 0, fov_deg: fov = DEFAULT_FOV_DEG } = placement || {};
  const apex = { x, y };
  return [apex, pointAlong(apex, rotation - fov / 2, length),
          pointAlong(apex, rotation + fov / 2, length)];
}

/** True when `point` falls inside the camera's drawn field of view. */
export function withinFieldOfView(placement, point) {
  const bearing = aimDegrees(placement, point);
  if (bearing === null) return true;
  const fov = placement?.fov_deg ?? DEFAULT_FOV_DEG;
  return Math.abs(normalizeDegrees(bearing - (placement?.rotation_deg ?? 0))) <= fov / 2;
}
