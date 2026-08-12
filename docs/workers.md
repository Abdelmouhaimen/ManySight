# Workers and observations

A StoreLens worker is an external process running where its camera, video, file, or
sensor is reachable. It owns capture, inference, tracking, and model-specific
preprocessing. StoreLens owns geometry enrichment and derived analytics.

## Worker lifecycle

1. Read the source and geometry needed for the task.
2. Resolve source access with the SDK or privileged connection endpoint.
3. Register a job describing the purpose, source IDs, and observation kinds.
4. Register a worker instance and heartbeat every 5–15 seconds.
5. Submit observations in batches of at most 5,000 rows; batches of 100–500 every
   1–5 seconds are a practical default.
6. Check each heartbeat response for a cooperative stop or restart request.
7. Verify `/observations/latest` and `/analytics/query`, then save an analysis if the
   result should appear in the dashboard.

A job is metadata. A worker instance is heartbeat-backed runtime state. StoreLens does
not start or relaunch arbitrary worker scripts.

## Opening a source

```python
from storelens import StoreLens

client = StoreLens("http://127.0.0.1:8000", api_key="")
source = client.source(1)
capture = client.open_capture(source)
```

For a managed webcam, the SDK can open the non-secret `device_index` directly. Other
managed source kinds use the privileged connection endpoint and need
`STORELENS_CREDENTIAL_ACCESS_KEY` (or the configured API key fallback). For an
external-secret source, `open_capture` resolves `source.locator.local_secret_ref` from
the worker environment. Passing `local_connection=` explicitly is a supported
worker-local override and takes precedence over both modes.

The worker must be able to reach the configured host or path from its own machine.
Do not log resolved connection objects or URLs assembled with credentials.

## Observation contract

Every observation has `schema_version: 2`, a worker-generated `observation_id`, a
timestamp, source ID, and one of these kinds:

- `detection`: an entity type plus spatial evidence such as `point_px`, `bbox_px`,
  keypoints, or a compressed mask. An opaque `entity_id` enables visits and dwell.
- `measurement`: one directly observed numeric value with a name, value kind, and
  optional unit. Do not submit a pre-aggregated dashboard total.
- `state`: the current categorical label for a state name. Send every sample,
  including repeated labels, so StoreLens can derive intervals and staleness.

The runtime contract is available at `GET /api/v1/observations/contract`.

Workers must not send `zone_id` or `zone`, or publish derived kinds such as
`zone_enter`, `zone_exit`, `zone_dwell`, `state_change`, or `count`. StoreLens rejects
those values on the schema-v2 endpoint.

## Presence including zero

Detections exist only when an entity is observed. For a zero-capable presence series,
submit one `measurement` named `detection_frame_count` for every processed frame. Set
its label to the entity type and its gauge value to the number of detections, including
zero. Give it exactly the same timestamp as that frame's detections. StoreLens does not
merge neighboring timestamps or synchronize cameras.

Treat the measurement as the completion marker for that processed frame: buffer the
frame's zero or more detections first, append `detection_frame_count` last, then flush.
Use one `sample_ts` value; do not call `time.time()` separately for each detection.
Do not skip the marker for an empty frame and do not create a fake zero-confidence
detection. A processed frame is a frame on which the worker actually ran its detector,
not every physical camera frame skipped by an intentional sampler.

`GET /api/v1/observations/latest-frames?entity_type=person` reconstructs the latest
completed frame per source from the marker and exact source/timestamp detections. The
scene persists until a newer marker arrives. Freshness is reported separately, so a
stopped worker makes the last frame stale without changing its contents.

## Spatial evidence

For camera workers, submit pixel evidence and let StoreLens project it. The
representative-point order is explicit point, feet/ankle keypoints, bounding-box
bottom-center, then no point for a mask-only observation. Bounding boxes use corner
form `[x0, y0, x1, y1]`.

`geometry.point_map` is available for trusted non-camera producers that already
measure a location in the map coordinate system. It is not a shortcut for workers to
perform their own camera calibration or zone assignment.

## Examples

- `examples/heatmap_tracker.py`: YOLO when installed, with a motion-subtraction
  fallback; submits detections and zero-capable frame counts.
- `examples/dwell_zones.py`: tracked detections and per-processed-frame counts used
  for platform-derived visits and dwell.
- `examples/fridge_state.py`: repeated open/closed state samples.
- `examples/simulate_children_counts.py`: synthetic measurement series for UI and
  contract testing.
- `examples/simulate_shoppers.py`: synthetic detections and states for a seeded demo.

The simulators are development tools, not model-quality demonstrations or required
runtime components.
