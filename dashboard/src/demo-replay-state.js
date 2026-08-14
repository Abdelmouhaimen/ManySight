export function frameIndexAt(videoTime, fps, frameCount) {
  const last = Math.max(0, Number(frameCount || 1) - 1);
  return Math.min(last, Math.max(0, Math.floor((Number(videoTime) + 1e-7) * Number(fps || 30))));
}

export function derivedSampleIndexAt(timeline = [], videoTime = 0) {
  let low = 0;
  let high = timeline.length - 1;
  let answer = -1;
  while (low <= high) {
    const middle = Math.floor((low + high) / 2);
    if (Number(timeline[middle].video_time_s) <= Number(videoTime) + 1e-9) {
      answer = middle;
      low = middle + 1;
    } else high = middle - 1;
  }
  return answer;
}

export function interpolateFusedEntities(timeline = [], index = -1, videoTime = 0, epoch = 0) {
  if (index < 0 || !timeline[index]) return [];
  const current = timeline[index];
  const next = timeline[index + 1];
  const nextById = new Map((next?.fused_entities || []).map((entity) => [entity.fused_entity_id, entity]));
  const span = next ? Number(next.video_time_s) - Number(current.video_time_s) : 0;
  const ratio = span > 0 ? Math.min(1, Math.max(0, (Number(videoTime) - Number(current.video_time_s)) / span)) : 0;
  return (current.fused_entities || []).map((entity) => {
    const following = nextById.get(entity.fused_entity_id);
    const point = entity.point_map;
    const interpolated = following?.point_map && point
      ? {
          x: point.x + (following.point_map.x - point.x) * ratio,
          y: point.y + (following.point_map.y - point.y) * ratio,
        }
      : point;
    return {
      ...entity,
      runtime_id: `e${epoch}:${entity.fused_entity_id}`,
      point_map: interpolated,
      interpolated: Boolean(following?.point_map && point && ratio > 0),
    };
  });
}

export function alertsAt(timeline = [], videoTime = 0, epoch = 0) {
  return timeline.flatMap((sample) => Number(sample.video_time_s) <= Number(videoTime) + 1e-9
    ? (sample.alert_events || []).map((event, index) => ({
        ...event,
        runtime_id: `e${epoch}:a${sample.index}:${index}`,
      }))
    : []);
}

export function replayStateAt(cache, videoTime, epoch = 0) {
  const timeline = cache?.timeline || [];
  const index = derivedSampleIndexAt(timeline, videoTime);
  const sample = index >= 0 ? timeline[index] : null;
  return {
    videoTime: Number(videoTime) || 0,
    epoch,
    frameIndex: frameIndexAt(videoTime, cache?.metadata?.source_fps, cache?.metadata?.media
      ? Math.min(...Object.values(cache.metadata.media).map((item) => item.frame_count)) : 1),
    derivedIndex: index,
    derivedSample: sample,
    entities: interpolateFusedEntities(timeline, index, videoTime, epoch),
    kpi: sample?.kpi || null,
    alerts: alertsAt(timeline, videoTime, epoch),
  };
}
