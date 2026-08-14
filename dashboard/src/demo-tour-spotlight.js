/* Spotlight mask geometry and target resolution for the guided-demo tour.
 *
 * Everything here is pure rectangle math over injected inputs: a document-like
 * root for target lookup and plain {top,left,width,height} rectangles for the
 * target and the viewport. The React layer owns listeners and measurement only,
 * which keeps the overlay behaviour testable without a browser.
 */

export const TOUR_TARGET_ATTRIBUTE = "data-demo-tour";
export const DEFAULT_PADDING = 10;
export const DEFAULT_RADIUS = 14;
export const CARD_MARGIN = 16;
/** A spotlight covering more of the viewport than this is a region, not a control. */
export const REGION_OBSTACLE_RATIO = 0.3;
/** Overlap this small (as a share of the card) is not worth moving the card for. */
export const TOLERATED_OVERLAP_RATIO = 0.12;
/* Enough attempts (~9s of backoff) for a route change plus the target view's own
 * data loading, so a slow page never turns into a false "control not found". */
export const MAX_TARGET_ATTEMPTS = 20;

export function tourTargetSelector(name) {
  return `[${TOUR_TARGET_ATTRIBUTE}="${name}"]`;
}

/** Resolve a tour target by stable hook, never by structural CSS position. */
export function resolveTourTarget(name, root) {
  if (!name || !root?.querySelector) return null;
  return root.querySelector(tourTargetSelector(name)) || null;
}

/** Backoff while a route transition or lazy view is still rendering. */
export function targetAttemptDelay(attempt) {
  return Math.min(400, 60 + attempt * 40);
}

export function targetAttemptsExhausted(attempt) {
  return attempt >= MAX_TARGET_ATTEMPTS;
}

function clamp(value, low, high) {
  return Math.min(high, Math.max(low, value));
}

function area(rect) {
  return Math.max(0, rect.width) * Math.max(0, rect.height);
}

export function rectsOverlap(a, b, margin = 0) {
  if (!a || !b) return false;
  return a.left - margin < b.left + b.width
    && b.left - margin < a.left + a.width
    && a.top - margin < b.top + b.height
    && b.top - margin < a.top + a.height;
}

function overlapArea(a, b) {
  if (!a || !b) return 0;
  const width = Math.min(a.left + a.width, b.left + b.width) - Math.max(a.left, b.left);
  const height = Math.min(a.top + a.height, b.top + b.height) - Math.max(a.top, b.top);
  return Math.max(0, width) * Math.max(0, height);
}

/**
 * The bright hole around a target: padded, viewport-clamped, and rounded.
 * Returns null when there is nothing meaningful to cut out, so callers can dim
 * the page uniformly instead of drawing a degenerate hole.
 */
export function spotlightGeometry(rect, options = {}) {
  const { padding = DEFAULT_PADDING, radius = DEFAULT_RADIUS, viewport } = options;
  if (!rect || area(rect) <= 0 || !viewport) return null;
  const padded = {
    top: rect.top - padding,
    left: rect.left - padding,
    width: rect.width + padding * 2,
    height: rect.height + padding * 2,
  };
  const top = clamp(padded.top, 0, viewport.height);
  const left = clamp(padded.left, 0, viewport.width);
  const bottom = clamp(padded.top + padded.height, 0, viewport.height);
  const right = clamp(padded.left + padded.width, 0, viewport.width);
  const width = right - left;
  const height = bottom - top;
  if (width <= 1 || height <= 1) {
    return { top, left, width: 0, height: 0, radius: 0, offscreen: true };
  }
  return {
    top, left, width, height,
    radius: Math.max(0, Math.min(radius, Math.min(width, height) / 2)),
    offscreen: false,
  };
}

/**
 * Click blockers for a required interaction: four rectangles around the hole.
 * The hole itself stays free, so the real control underneath keeps working.
 */
export function blockerRects(hole, viewport) {
  if (!viewport) return [];
  if (!hole || hole.offscreen || area(hole) <= 0) {
    return [{ top: 0, left: 0, width: viewport.width, height: viewport.height }];
  }
  const bottom = hole.top + hole.height;
  const right = hole.left + hole.width;
  return [
    { top: 0, left: 0, width: viewport.width, height: hole.top },
    { top: bottom, left: 0, width: viewport.width, height: viewport.height - bottom },
    { top: hole.top, left: 0, width: hole.left, height: hole.height },
    { top: hole.top, left: right, width: viewport.width - right, height: hole.height },
  ].filter((rect) => area(rect) > 0);
}

const PLACEMENTS = ["top-left", "top-right", "bottom-left", "bottom-right"];

function placementRect(placement, card, viewport, inset) {
  const width = Math.min(card.width, Math.max(0, viewport.width - inset.left - inset.right));
  const height = Math.min(card.height, Math.max(0, viewport.height - inset.top - inset.bottom));
  const left = placement.endsWith("left") ? inset.left : viewport.width - inset.right - width;
  const top = placement.startsWith("top") ? inset.top : viewport.height - inset.bottom - height;
  return { placement, top: Math.max(0, top), left: Math.max(0, left), width, height };
}

/**
 * Keep the progress card off the things the step is about: the spotlighted
 * element and any open StoreLens dialog. Preference order starts at the
 * documented top-left position below the existing header area; the caller's
 * inset keeps it clear of persistent chrome such as the sidebar.
 *
 * The card is meant to feel parked. Two rules keep it still: a spotlight that
 * covers most of the viewport is a region rather than a control, so there is
 * nothing to dodge, and a slight clip of an obstacle is tolerated instead of
 * chasing corners. Pass the current placement as `preferred` for hysteresis.
 */
export function cardPlacement({ card, hole, obstacles, viewport, inset, preferred = "top-left" } = {}) {
  if (!card || !viewport) return null;
  const viewportArea = Math.max(1, viewport.width * viewport.height);
  const blocked = [...(obstacles || []), hole]
    .filter(Boolean)
    .filter((obstacle) => area(obstacle) / viewportArea <= REGION_OBSTACLE_RATIO);
  const tolerated = area(card) * TOLERATED_OVERLAP_RATIO;
  const box = {
    top: inset?.top ?? 84,
    right: inset?.right ?? CARD_MARGIN,
    bottom: inset?.bottom ?? CARD_MARGIN,
    left: inset?.left ?? CARD_MARGIN,
  };
  const order = [preferred, ...PLACEMENTS.filter((value) => value !== preferred)];
  const candidates = order.map((placement) => placementRect(placement, card, viewport, box));
  const cost = (candidate) => blocked.reduce((total, obstacle) =>
    total + overlapArea(candidate, obstacle), 0);
  const clear = candidates.find((candidate) => cost(candidate) <= tolerated);
  if (clear) return clear;
  return candidates.reduce((best, candidate) =>
    cost(candidate) < cost(best) ? candidate : best, candidates[0]);
}

/** SVG mask path: full viewport with the spotlight hole cut out. */
export function maskPath(hole, viewport) {
  if (!viewport) return "";
  const outer = `M0 0H${viewport.width}V${viewport.height}H0Z`;
  if (!hole || hole.offscreen || area(hole) <= 0) return outer;
  const { top, left, width, height, radius } = hole;
  const r = Math.min(radius, width / 2, height / 2);
  return `${outer} M${left + r} ${top}H${left + width - r}`
    + `A${r} ${r} 0 0 1 ${left + width} ${top + r}`
    + `V${top + height - r}A${r} ${r} 0 0 1 ${left + width - r} ${top + height}`
    + `H${left + r}A${r} ${r} 0 0 1 ${left} ${top + height - r}`
    + `V${top + r}A${r} ${r} 0 0 1 ${left + r} ${top}Z`;
}

export function isRectVisible(rect, viewport) {
  if (!rect || !viewport) return false;
  return rect.top < viewport.height && rect.top + rect.height > 0
    && rect.left < viewport.width && rect.left + rect.width > 0;
}
