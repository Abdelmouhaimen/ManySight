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
