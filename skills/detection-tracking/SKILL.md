---
name: detection-tracking
description: Use for people/object presence, heatmaps, visits, dwell, flow, and any worker that submits tracked spatial detections, including complete zero-capable samples.
---

# Detection and tracking

StoreLens derives presence, density, visits, dwell, and transitions from the same stream
of tracked `detection` observations. Workers never submit canonical zones, enter/exit,
dwell, occupancy, transitions, or dashboard aggregates.

## Complete samples

For each processed frame:

1. Choose one exact timestamp and one opaque source-local `sample_id`.
2. Detect and track objects. `entity_id` is an anonymous tracker ID with an honest
   `identity_scope`, not a verified person identity.
3. Build one `DetectionSample` containing every detection with pixel evidence. For
   floor traffic, feet or bbox bottom-center are normally correct; preserve
   bbox/keypoints/mask when available.
4. Submit the complete envelope once. Use `detections=[]` for a processed empty frame.
5. Never invent a zero-confidence detection for an empty frame.

Prefer the SDK builder:

```python
sample = client.begin_detection_sample(source_id, "person", ts=time.time())
for track in tracks:
    sample.add_detection(
        entity_id=str(track.id),
        bbox_px=(track.x0, track.y0, track.x1, track.y1),
        confidence=track.confidence,
    )
sample.submit()
```

The builder sends the preferred atomic detection-sample envelope. The SDK also exposes
`submit_detection_sample(...)`. Legacy split detection rows plus a matching
`detection_frame_count` measurement remain readable, but new workers should not author
that internal completion concept. Do not call `time.time()` separately for detections
from one sample.

## Workflow

1. Load `storelens-platform`; inspect the source, map, calibration, zone views, and
   projection surfaces.
2. Capture locally with `StoreLens.open_capture`. Never log resolved credentials.
3. Register a job and concrete worker, heartbeat every 5-15 seconds, and obey stop/restart.
4. Use an appropriate detector and tracker. Model choice remains worker-local.
5. Verify source current samples, projection, assigned zone, and freshness.
6. If cameras overlap, load `multiview` and validate fused member evidence. Workers still
   submit independent source-local identities.
7. Preview the analytical question with `query_data`; save it with
   `create_saved_query`. Load `generated-dashboard` only when a persistent view is wanted.

## Pitfalls

- Unstable tracker IDs destroy dwell and flow semantics.
- A torso projected through a floor homography is usually wrong; use feet or a suitable
  named plane.
- Missing samples mean stale/unknown evidence, not observed zero.
- 1-2 processed samples per second is often sufficient for spatial analytics; tune to
  the use case rather than uploading every camera frame by default.
- Geometry-only multiview association is not biometric ReID and can switch at close
  crossings or under poor calibration/synchronization.
