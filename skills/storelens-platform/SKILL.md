---
name: storelens-platform
description: General operating guide for StoreLens. Use first for every StoreLens request, especially when the user gives a short outcome-oriented prompt, the agent is connected only through MCP, or the task involves cameras, zones, workers, observations, analytics, alerts, or insights. Explains the platform purpose, MCP versus REST responsibilities, API discovery, default workflow, event contract, validation, and safety boundaries before any task-specific skill is applied.
---

# StoreLens platform guide

Use this skill before any task-specific StoreLens playbook. Treat the live platform,
not prior conversation or demo assumptions, as the source of truth.

## Understand the platform

StoreLens is infrastructure for computer-vision analysis of physical spaces. It stores
logical non-secret source descriptors, a floor map, named global zones, floor calibration, named
projection planes, per-camera zone views/decision ROIs, analysis and worker-instance
registrations, raw observations, derived analytics, alerts, and structured insight
definitions.

StoreLens does not choose a model or execute arbitrary CV scripts. It exposes worker
registration, heartbeat, staleness, and cooperative stop/restart commands; a deployment
supervisor still owns the actual process. Act as the analysis operator: understand the
question, inspect the configured space, choose or build a worker, run it where it can
reach the camera, and submit observations.

Keep the three surfaces distinct:

- **MCP** lets an agent discover and configure StoreLens and verify results.
- **REST API** is the stable interface workers use to register and submit data.
- **Dashboard** lets people configure the space and inspect detections, insights, and
  reviewable signals.

A worker must not require MCP. MCP is an agent adapter, not a camera subscriber or
worker runtime.

Camera access is agent-local. StoreLens never opens a feed, captures a snapshot, or
returns a camera URL/password. A logical source may advertise a safe `device_index` or
`local_secret_ref`; resolve the real connection from an environment variable, keychain,
or ignored file on the device where the worker runs.

## Find the API

Start with `get_platform_config`. It resolves the editable `config/endpoints.json`
profile plus deployment overrides and is authoritative for the current connection.
If it is unavailable, use the StoreLens URL supplied by the MCP/client configuration
or by the user.

- Interactive OpenAPI: `{STORELENS_URL}/docs`
- REST base: `{STORELENS_URL}/api/v1`
- Health check: `{STORELENS_URL}/api/v1/health`

Treat OpenAPI as authoritative for endpoints, fields, query parameters, response
shapes, and validation errors. Use MCP tools for agent operations. Consult `/docs` when
a worker needs an endpoint or when MCP has no tool for an operation. If API-key auth is
enabled, use the configured key through `X-API-Key`; never embed it in code or logs.

## Interpret short user prompts

Users may state only an outcome, for example, "count people in the hall" or "tell me
when the medicine shelf is empty." Do not require them to know StoreLens internals.
Translate the outcome into the workflow below, choose sensible reversible defaults,
and ask only for decisions that materially affect meaning, privacy, geometry, cost, or
external side effects.

Detailed user requirements override defaults when they remain safe and compatible with
the observation contract.

## Follow the default workflow

1. **Discover.** Call `list_sources`, `get_store_map`, and `list_zones`. If a source is
   missing, call `create_source` with non-secret local hints. Inspect relevant frames
   directly on the worker device. Never invent source IDs, zones, camera coverage,
   placement, or calibration.
2. **Clarify the measurement.** State what an object, count, visit, state, or alert will
   mean. Distinguish anonymous tracks from unique people and model output from fact.
3. **Load a recipe.** Call `list_skills` and then `get_skill` for the closest specialized
   playbook. Compose playbooks when necessary.
4. **Confirm geometry.** Keep the global map footprint separate from its camera view.
   Inspect a locally captured frame, projection surfaces, and zone views. For a new zone, confirm
   the map footprint. For each camera, confirm the visible outer polygon and inset
   decision ROI. If the target is elevated and planar (mattress, table, shelf), create
   a named plane from at least four `{px,map}` pairs. Never subtract physical height
   from map Y. A zone creates no behavior by itself.
5. **Plan a bounded pilot.** Select the simplest adequate model, validation method,
   sampling rate, run duration, and stop condition. Reuse existing workers and insight
   definitions when appropriate.
6. **Register.** Call `register_job` before submitting anything and retain its `job_id`.
7. **Run and observe.** Run the worker outside the dashboard. Register a worker instance,
   heartbeat every 5–15 seconds, obey `should_stop`/`restart_requested`, and post raw
   observations in batches through `POST /api/v1/events` or `submit_events`.
8. **Verify.** Query `get_events(job_id=...)`, inspect Detections, and call the relevant
   `get_analytics` endpoint. Check timestamps, source attribution, stable tracks,
   projection, zone assignment, sampling rate, and obvious false positives.
9. **Publish only when useful.** Call `list_insights` and
   `list_insight_templates` before `register_insight`. Avoid duplicates, state honest
   limitations, and pin only when requested or clearly appropriate.
10. **Report operation honestly.** Report what ran, where it ran, job/model/version,
    event counts, validation performed, limitations, and how to stop or restart it.

## Post observations, not conclusions

Use the event schema documented by OpenAPI. Common fields are:

| field | use |
|---|---|
| `ts` | observation time; omit only when ingestion time is acceptable |
| `source_id` | camera that produced the observation |
| `event_type` | `detection`, `zone_enter`, `zone_exit`, `state_change`, `count`, `transition`, or `custom` |
| `track_id` | anonymous stable per-run object ID |
| `point_px` | camera-pixel representative point; feet are the floor-plane default |
| `point_map` | map metres when the worker already projected a point |
| `bbox` | `[x,y,w,h]` pixel evidence; preserved and used by overlap rules |
| `keypoints` | pose/object keypoint evidence; preserved and usable by zone-view rules |
| `mask` | optional compressed/RLE segmentation evidence; preserved, not expanded |
| `point_kind` | meaning of the point: feet, hip/torso centre, mask centroid, custom |
| `projection_surface_id` | named plane for an elevated planar target; omit for floor |
| `zone_view_id` | explicit camera ROI provenance when needed; usually auto-matched |
| `zone_id` / `zone` | explicit zone; otherwise let calibrated projection assign it |
| `value` | raw numeric sample, such as a per-frame count |
| `label` | observed class or state |
| `attributes` | free model/domain metadata such as confidence, model version, or product ID |

Put domain-specific fields in `attributes`; arbitrary top-level fields are not part of
the contract. Do not assume an attribute automatically becomes a filter or insight.

Ingestion records `projection_method`, `zone_assignment_method`, and the zone,
calibration, surface, and zone-view revisions. This provenance appears in Detections.
Editing geometry affects future rows only; never rewrite historical evidence silently.

Examples of the derive-only rule:

- Post person positions, not a heatmap.
- Post `zone_enter` and `zone_exit`, not calculated dwell. `zone_dwell` is deprecated.
- Post label-only `state_change` flips, not state durations.
- Post per-frame `count` samples, not cumulative totals.

The platform derives heatmaps, occupancy, dwell, flow, state duration, alerts, and
registered insight views from these observations.

## Choose geometry by what the point physically touches

- **Standing/walking on the floor:** post feet/bbox-bottom-centre and use floor
  calibration.
- **Lying or sitting on a known planar surface:** define a named projection surface,
  attach it to the zone view, and post a representative point on that plane (for
  example hip/torso centre) or let the ROI assign the zone from bbox/keypoints.
- **Presence in a visible region:** use a zone view. `point` tests one representative
  point, `bbox_overlap` requires the configured fraction of the box in the inset ROI,
  and `keypoints_inside` combines an inside fraction with `min_keypoints`.
- **Map footprint to camera proposal:** call `unproject_points` with the selected
  surface, then inset the returned polygon and confirm it against a locally captured frame.
- **Non-planar 3D requirement:** do not improvise a pixel or map offset. Explain that
  intrinsics/extrinsics and ray–plane or 3D reconstruction are required.

Use `get_store_map`, `list_projection_surfaces`, and `list_zone_views` to reuse current
geometry. Update definitions in place so their revisions increment; historical events
retain the revisions that produced them.

## Build workers conservatively

- Prefer a lightweight, supported model that directly measures the requested concept.
- Keep a worker and its virtual environment in the user-designated workspace or edge gateway.
- Store configuration in environment variables or ignored local files; never print
  camera credentials or API keys.
- Use anonymous tracking and stable per-run IDs when tracking is required.
- Sample detections around 1-2 Hz per track unless the measurement needs another rate.
- Batch submissions, handle disconnects, retry with bounds, and flush on shutdown.
- Call `register_worker` after the process actually starts. Heartbeat every 5–15 seconds,
  include useful metrics such as FPS/queue depth, and exit cleanly when instructed.
- Start with a short run and explicit stop condition. Do not run indefinitely unless
  the user asks for continuous operation.
- Record model name/version, confidence, and useful validation metadata in `attributes`.
- Degrade explicitly when dependencies or camera access are unavailable; do not pretend
  that a fallback measures the same concept with equal accuracy.

Remember that job status is registration metadata. `latest_worker.effective_status`
is heartbeat-backed, but restart still requires a process supervisor. Source health is
derived from event ingestion and heartbeats, not from a platform camera probe.

## Protect meaning, privacy, and trust

- Avoid identification and sensitive-trait inference unless explicitly authorized and
  appropriate for the deployment.
- Describe tracks as tracks, not confirmed unique people.
- Explain geometry limitations. Feet plus floor calibration is appropriate for standing
  floor traffic. Sitting, lying, occluded, or elevated subjects need an appropriate
  representative point and usually a camera zone view; planar elevated targets need a
  named surface. Arbitrary 3D localization needs camera intrinsics/extrinsics and is not
  solved by a 2D homography.
- Confirm consequential alerts, webhooks, or other external actions before creating
  them unless the user explicitly requested them.
- Keep observations traceable in Detections and present derived conclusions as model
  estimates with limitations.

## Choose specialized playbooks

After this guide, load the closest available skill:

- `heatmap` for person/object positions and spatial activity.
- `dwell-time` for enter/exit tracking and time in zones.
- `state-monitoring` for equipment or scene-state changes.
- `alerts-workflows` for thresholds, review signals, and webhooks.
- `insights` for publishing verified results to the dashboard catalogue.

If no specialized skill fits, use OpenAPI and the raw-observation contract as the
boundary. Implement the narrowest reversible pilot, verify it, and document the new
pattern before treating it as reusable.
