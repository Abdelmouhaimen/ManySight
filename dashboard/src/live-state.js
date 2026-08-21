// Pure latest-frame state helpers. Kept separate from React/Three.js so the
// source-local frame semantics can be exercised with Node's built-in test runner.

export function sourceLocalTrackKey(observation) {
  const entity = observation.entity_id || `observation-${observation.id}`;
  const producer = observation.worker_id || observation.job_id || "worker";
  return `${observation.source_id}:${producer}:${entity}`;
}

export function reconcileCompletedFrames(currentFrames = [], incomingFrames = []) {
  const bySource = new Map(currentFrames.map((frame) => [String(frame.source_id), frame]));
  for (const frame of incomingFrames) {
    const key = String(frame.source_id);
    const current = bySource.get(key);
    if (!current || frame.timestamp >= current.timestamp) bySource.set(key, frame);
  }
  return [...bySource.values()].sort((left, right) => left.source_id - right.source_id);
}

export function latestFrameTracks(frames = []) {
  const tracks = new Map();
  for (const frame of frames) {
    for (const observation of frame.detections || []) {
      if (
        observation.entity_type !== "person"
        || !observation.entity_id
        || !observation.geometry?.point_map
      ) continue;
      const key = sourceLocalTrackKey(observation);
      // A malformed producer can repeat a track in one frame. The UI still
      // renders one source-local entity instead of duplicating it.
      tracks.set(key, {
        key,
        rows: [observation],
        position: { ...observation.geometry.point_map, observation },
        opacity: 1,
        colorKey: key,
        trail: [],
        frame,
      });
    }
  }
  return [...tracks.values()];
}

export function frameIsStale(frame, nowSeconds) {
  if (frame.source_last_ingestion_at == null) return true;
  return nowSeconds - frame.source_last_ingestion_at > (frame.stale_after_s || 30);
}

export function combinedModeAvailable(fused = {}) {
  return Array.isArray(fused.groups) && fused.groups.length > 0;
}

export function availableIdentityMode(currentMode, fused = {}) {
  return currentMode === "fused" && !combinedModeAvailable(fused) ? "source" : currentMode;
}

/* ------------------------------------------------- presentation-only geometry
 *
 * The 3D scene tints a zone red while a rendered track sits inside it. That test
 * runs in the browser, over interpolated presentation positions, against
 * whichever zones happen to be loaded — it is a *lighting cue*, nothing more.
 *
 * Authoritative occupancy comes from the server: current-state materialization
 * and the saved-query engine, which carry their own known/partial/unknown
 * quality. Those two numbers can legitimately disagree, and when they do the
 * server is right.
 *
 * So this helper deliberately returns a Set of zone IDs to paint and never a
 * count. Do not add a `.size` shortcut here, do not surface its result as a
 * number, and do not feed it into a result card, an alert or a query.
 */

export function pointInPolygon(point, polygon = []) {
  if (!point || polygon.length < 3) return false;
  let inside = false;
  for (let current = 0, previous = polygon.length - 1; current < polygon.length; previous = current++) {
    const a = polygon[previous];
    const b = polygon[current];
    const cross = (point.y - a.y) * (b.x - a.x) - (point.x - a.x) * (b.y - a.y);
    const onSegment = Math.abs(cross) < 1e-8
      && point.x >= Math.min(a.x, b.x) && point.x <= Math.max(a.x, b.x)
      && point.y >= Math.min(a.y, b.y) && point.y <= Math.max(a.y, b.y);
    if (onSegment) return true;
    const crossesRay = ((a.y > point.y) !== (b.y > point.y))
      && point.x < ((b.x - a.x) * (point.y - a.y)) / (b.y - a.y) + a.x;
    if (crossesRay) inside = !inside;
  }
  return inside;
}

/**
 * Which zones the scene should tint. Presentation only — see the note above.
 *
 * @returns {Set<string>} zone IDs to highlight. Never a count.
 */
export function highlightedZoneIds(renderedTracks = [], zones = [], ringsOf = () => []) {
  const highlighted = new Set();
  for (const track of renderedTracks) {
    for (const zone of zones) {
      const serverAssigned =
        track.position?.observation?.zone_id != null
        && Number(track.position.observation.zone_id) === Number(zone.id);
      if (serverAssigned || ringsOf(zone).some((ring) => pointInPolygon(track.position, ring))) {
        highlighted.add(String(zone.id));
      }
    }
  }
  return highlighted;
}
