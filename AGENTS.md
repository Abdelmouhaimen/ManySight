# StoreLens — agent operating manual

You (Codex, or any coding agent) are the **analysis brain** of StoreLens. The platform is
deliberately dumb about computer vision: it stores logical source descriptors, a floor plan with
named global zones, floor and named-plane homographies, per-camera zone views/decision ROIs,
heartbeat-backed worker instances, and a generic stream of raw observations it turns into
analyses (presence, dwell, flow, states, measurement trends) and alerts. **You** pick the models,
write the worker scripts, run them, and post observations back.

Camera access is worker-local. StoreLens never opens or proxies a feed or captures a
snapshot. A source can use `storelens_managed` structured connection settings with
encrypted credentials, or `external_secret` with `locator.local_secret_ref`. Ordinary
source reads never reveal credentials. Only an explicitly authorized worker or MCP client
may call the privileged connection-resolution endpoint, and it must use the result only
in memory to open the source locally.

## Observe locally, derive centrally

This is the one rule everything else follows from: **a worker submits only three
observation kinds — `detection`, `measurement`, `state` — and StoreLens derives
everything else.** Call `get_observation_contract()` for the exact field-level contract.

A worker may:
- Open a camera, video, or local stream and run whatever detection/tracking/pose/
  classification/counting/state-recognition model fits the question.
- Assign an opaque `entity_id` when tracking or re-identification is available. This is
  never a verified human identity — declare an `identity_scope` (`worker_run` [default],
  `source`, or `workspace`) so StoreLens knows how far it's safe to treat two IDs as "the
  same," and never invent cross-camera identity by joining similar IDs or attributes.
- Send pixel-space evidence (`point_px`, `bbox_px`, `keypoints_px`, a compressed mask) or a
  directly observed numeric value or current state.
- Heartbeat and obey stop/restart requests.

A worker must **never**:
- Resolve a platform zone, or send `zone_id`/`zone`. StoreLens assigns zones from
  geometry at ingestion — a worker that tries is rejected with `legacy_derived_observation`.
- Detect zone entry/exit as a business event, calculate dwell, calculate occupancy, or
  calculate movement between zones. Submit tracked `detection` rows; StoreLens derives
  visits, dwell, transitions, and presence from them.
- Send a `state_change` event or calculate a state's duration. Submit a `state` sample on
  every reading — including runs of identical samples — and let StoreLens coalesce them
  into intervals and derive transitions/durations itself.
- Calculate any other analytics or dashboard value, or decide which chart a result should
  render as.

## The contract

1. **Discover** — `list_sources`, `get_store_map`, `list_zones`, `list_projection_surfaces`,
   and `list_zone_views`.
2. **Load the platform guide, then pick a recipe** — read `storelens-platform` first,
   then `list_skills()` → `get_skill(name)`. Skills live in `skills/`; follow the closest
   task playbook and compose them for multi-part requests.
3. **Register a job** — `register_job(name, description, source_ids, event_types)` *before*
   posting anything. Keep the returned `job_id`.
4. **Run analysis & submit observations** — connect to and inspect the camera locally,
   write a worker script (use `sdk/python/storelens.py`), run it, register its worker
   instance, heartbeat every 5–15 seconds, obey stop/restart flags, and
   `submit_observations` in batches (≤5000; 100–500 is a good size, every 1–5 s).
5. **Verify & publish** — `get_latest_observations()` and `query_analytics(...)` to confirm
   the data looks right, then `create_analysis(...)` so the result appears on the dashboard
   as a saved question (`list_analysis_capabilities()` shows which subjects/measures/
   groupings fit the data actually present). One analysis per question — switching how it
   renders is a `presentation` patch on the same record, never a second one. Raw rows are
   browsable in the **Observations** tab.

## Observation envelope (the one thing to get right)

Every observation has these common fields, plus kind-specific ones:

| field | meaning |
|---|---|
| `schema_version` | `2` |
| `observation_id` | worker-generated idempotency key — retries are safe |
| `kind` | `detection` \| `measurement` \| `state` |
| `timestamp` | epoch seconds or ISO-8601 |
| `source_id` | which camera/sensor produced it |
| `worker_id` / `job_id` | optional, from register_worker/register_job |
| `confidence` | optional model confidence |
| `entity_id` | opaque per-track id (detection; optionally measurement/state) |
| `identity_scope` | `worker_run` (default) \| `source` \| `workspace` |
| `attributes` | free dict — e.g. `{"gender":"female"}`. Any key becomes an Analytics split dimension. |

**detection**: `entity_type` (e.g. "person"), `label` (observed role/class, e.g.
"customer"), `geometry: {point_px:[x,y], bbox_px:[x0,y0,x1,y1], keypoints_px:{name:[x,y]},
mask}`. Representative-point precedence is deterministic: explicit `point_px`, then
foot/ankle keypoints, then bbox bottom-center, then left empty if only a mask is given.
`bbox_px` is corner form `[x0,y0,x1,y1]`, not `[x,y,w,h]`.

**measurement**: `name` (the metric, e.g. "queue_length"), `value`, `value_kind`
(`gauge` [default, instantaneous] \| `delta` \| `cumulative`), `unit`, optional `label`
(an instance qualifier, e.g. "checkout_queue"). Never post a time-aggregated or
precomputed total. A measurement is only zone-assigned if it carries geometry or shares an
`entity_id` with a recent detection.

**state**: `name` (the state key, e.g. "door_state"), `label` (the observed value, e.g.
"open"), `info` (free dict). Set `entity_id` when more than one independently stateful
entity shares a source and name (e.g. two fridges on one camera).

The stored row also records projection/assignment methods and the revisions of the zone,
floor calibration, projection surface, and zone view used at ingestion. Geometry edits
affect future rows; never silently reinterpret historical detections.

Conventions that make analyses light up:
- **Presence / heatmap / density** ← `detection` rows with a point. For zero-capable
  presence series, add one `measurement` named `detection_frame_count` per processed
  frame (`label` = entity type, `value` = frame detection count including 0) using the
  exact same timestamp as that frame's detections. Each measurement is an instantaneous
  sample; StoreLens does not merge neighboring timestamps or synchronize cameras.
- **Visits / dwell** ← ordinary tracked `detection` rows in a zone. StoreLens groups
  consecutive same-zone detections per entity into a visit (bridging brief gaps, requiring
  a minimum number of confirmed samples so one noisy frame at a boundary is never a
  confirmed entry), including in-progress visits, capped at 1 h.
- **Flow / transitions** ← the same tracked `detection` rows, read as a per-entity zone
  sequence.
- **State timeline / durations** ← `state` samples with `label`, sent every reading,
  including repeats. A source/name/entity whose most recent sample is older than the
  staleness timeout reports as stale rather than looking verified forever.
- **Alerts** ← `create_alert_rule` — the legacy kinds (`dwell_exceeds`, `occupancy_exceeds`,
  `state_alert`, `event_match`) still work, plus the general `analysis_condition` kind
  (an analysis + `{operator, value, for_seconds}`). Every time-based condition is
  evaluated on a periodic timer independent of ingestion, so a quiet zone or a stale
  source still gets caught. `webhook_url` POSTs alert JSON to n8n/Zapier/anything.
- **Analyses** ← `create_analysis(name, subject, measures, filters, grouping, ...)` — the
  Analytics tab renders only saved analyses (subjects: `detection` \| `measurement` \|
  `state`; grouping: none/`time`/`zone`, optionally split by label/entity/source/attribute).

## Zones from camera views

Separate the physical zone from how one camera sees it:

1. Capture and inspect a frame directly on the worker device, then call
   `get_store_map()` for the current global footprint. Confirm the footprint with the
   user before creating/updating it. Never upload the frame unless the user explicitly
   chooses to retain visual evidence.
2. A **zone** is the canonical physical polygon in map metres. `polygon_px + source_id`
   is only a shortcut for points on the calibrated floor plane.
3. A **zone view** belongs to one zone and one camera. Store the visible outer polygon,
   an inset detection ROI, and one membership rule: `point`, `bbox_overlap`, or
   `keypoints_inside`. Use `unproject_points` to propose camera pixels from the map
   footprint, then check the result against a frame captured on the worker device.
4. For a mattress, table, shelf, conveyor, or other elevated planar target, create a
   **projection surface** from at least four matching `{px,map}` points and attach it to
   the zone view. Height is metadata. Never subtract height from map Y; a homography
   maps one 2D plane to another and contains no vertical coordinate.
5. The `ztype` ("restricted", "queue", ...) remains only a semantic label. Alerts are
   separate platform configuration.
6. Workers submit evidence (`bbox_px`, keypoints/mask, and a point) via `detection`
   observations. The server preserves that evidence, selects the relevant zone view/plane,
   projects, assigns the zone, and records all definition revisions — a worker never
   resolves or sends a zone itself.

For an uncalibrated camera, a zone view can still assign by pixel-space bbox/keypoints.
Full non-planar 3D localization requires camera intrinsics/extrinsics and is outside the
current plane-homography model.

## Working in this repo

- Server: `uvicorn server.app:app` → UI at http://localhost:8000, OpenAPI at `/docs`.
- Worker SDK: `sdk/python/storelens.py` (requests + optional OpenCV). Copy or import it.
  `submit_detection`/`submit_measurement`/`submit_state` are the primary helpers;
  `add_event`/`post_events` (the legacy `/events` contract) still work but emit a
  `DeprecationWarning` for the kinds StoreLens now derives itself.
- A job is metadata. A worker instance is heartbeat-backed runtime state. Dashboard
  stop/restart commands are cooperative; a deployment supervisor performs relaunch.
- Examples to crib from: `examples/` (simulator, motion-based heatmap/dwell tracker,
  fridge state, a synthetic measurement curve) — every one of them submits only
  detection/measurement/state observations now.
- If OpenCV/ultralytics are unavailable, degrade gracefully: background-subtraction blobs
  (see `examples/heatmap_tracker.py`) still produce usable heatmaps and tracks.
- Zones missing? Propose them from frames captured locally by the worker and register with
  `create_zone` (see "Zones from camera views"), or ask the user to draw them in
  **Setup → Space & zones**. Calibration missing? Ask the user to calibrate, or fall back
  to a `geometry.point_map` you compute yourself (documented as a trusted-producer path,
  not the default for camera workers).
- `POST /api/v1/events` and the legacy per-event-type contract (`zone_enter`/`zone_exit`/
  `zone_dwell`/`state_change`/`count`) still exist as a documented compatibility surface
  for old integrations, and historical rows in that shape remain queryable — but new
  agent work must use `submit_observations`/`POST /observations/batch`, never
  `submit_events`/`POST /events`.

## Skills index

| skill | use when the user asks for |
|---|---|
| `storelens-platform` | any StoreLens task; load first for platform purpose, API discovery, workflow, and safety defaults |
| `detection-tracking` | presence, heatmaps, popularity, or anything needing tracked people/objects with coordinates |
| `measurement` | a numeric reading over time — counts, queue length, any classifier output |
| `state-observation` | open/closed, on/off states of equipment (fridge doors, lights) and their durations |
| `analytics` | saving a data question on the dashboard, pinning it, or curating saved analyses |
| `alerts-workflows` | notifications, thresholds, loitering, crowding, webhook/n8n integrations |
| `geometry-calibration` | zones, zone views, projection surfaces, camera calibration |
