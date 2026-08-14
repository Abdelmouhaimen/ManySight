# Workers and observations

A StoreLens worker is an external process running where its camera, video, file, or
sensor is reachable. It owns capture, inference, tracking, and model-specific
preprocessing. StoreLens owns geometry enrichment and derived analytics.

## Before writing a worker

`GET /api/v1/agent/worker-recipe` returns the current integration contract, generated
from the running platform: preferred endpoint and envelope, empty-frame semantics,
identity rules, forbidden output, sampling guidance, lifecycle expectations, and how to
verify. Fetch it — and `GET /api/v1/agent/perception` to check whether compatible
perception already exists — before building anything.

**Do not infer the contract from an example or demo script in a repository.** Files on
disk may predate the current API; the recipe, `GET /api/v1/observations/contract`, and
`/openapi.json` are authoritative.

## Worker lifecycle

1. Read the source and geometry needed for the task.
2. Resolve source access with the SDK or privileged connection endpoint.
3. Register a job describing the purpose, source IDs, and observation kinds.
4. Register a worker instance and heartbeat every 5–15 seconds. Report `local_fps` and
   `submission_hz` in heartbeat metrics so capability inspection can show them.
5. Submit one atomic `DetectionSample` per processed frame. Local detection and tracking
   may run at full camera FPS; the central submission rate is a separate, task-driven
   choice and is normally lower. For non-detection kinds, submit observations in batches
   of at most 5,000 rows; batches of 100–500 every 1–5 seconds are a practical default.
6. Check each heartbeat response for a cooperative stop or restart request.
7. Verify source-local and, where configured, fused current state — heartbeat, freshness,
   complete samples, projection, and zone assignment. Create a saved query and dashboard
   widget only after the observations are correct.

A job is metadata. A worker instance is heartbeat-backed runtime state. StoreLens does
not start or relaunch arbitrary worker scripts.

The guided demo is not a worker. Offline fixture generation submits versioned raw
`DetectionSample` records through the real platform pipeline and commits the resulting
derived replay cache. Ordinary playback reads that cache on one media clock, creates no
worker heartbeat rows, and performs neither inference nor live fusion.

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

`sample_id` is an opaque source-local frame/sample key. Continuous detection workers
should prefer the atomic `POST /api/v1/detection-samples` envelope. The lower-level
observation batch remains available for advanced producers and backward compatibility.
The runtime contract is available at `GET /api/v1/observations/contract`.

Workers must not send `zone_id` or `zone`, or publish derived kinds such as
`zone_enter`, `zone_exit`, `zone_dwell`, `state_change`, or `count`. StoreLens rejects
those values on the schema-v2 endpoint.

## Complete detection samples, including zero

One processed camera frame is one atomic `DetectionSample` containing zero or more
detections. The envelope carries one source, entity type, exact timestamp, opaque
`sample_id`, optional source `frame_index`, and shared attributes. Submit an empty list
for an observed zero; never create a fake zero-confidence detection. A processed frame
is a frame on which the detector actually ran, not every physical frame skipped by an
intentional sampler.

```python
client.submit_detection_sample(
    source_id=source_id,
    entity_type="person",
    sample_id=f"camera-{source_id}-frame-{frame_index}",
    timestamp=timestamp,
    frame_index=frame_index,
    detections=detections,  # [] is a complete known-zero sample
)
```

The SDK builder offers the same API incrementally in memory before one atomic submit.
StoreLens internally normalizes the envelope into entity observations and a private
completion record used by existing materializers. Workers do not author that record.
Legacy producers may still submit detection rows plus one `detection_frame_count`
measurement with matching source, entity type, timestamp, and `sample_id`; incomplete
or count-mismatched legacy samples do not advance current state.

`GET /api/v1/observations/latest-frames?entity_type=person` reconstructs the latest
complete frame per source. The
scene persists until a newer marker arrives. Freshness is reported separately, so a
stopped worker makes the last frame stale without changing its contents.

Workers never fuse identities. StoreLens performs geometry-first association only after
complete samples are materialized for sources in an explicit multiview group. A worker
continues to use its own opaque, scope-limited tracker IDs.

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
