# StoreLens — agent operating manual

You (Codex, or any coding agent) are the **analysis brain** of StoreLens. The platform is
deliberately dumb about computer vision: it stores camera sources, a floor plan with named
global zones, floor and named-plane homographies, per-camera zone views/decision ROIs,
heartbeat-backed worker instances, and a generic stream of raw
observations it turns into insights (heatmaps, dwell, flow, states, alerts). **You** pick the
models, write the worker scripts, run them, and post observations back.

## Observations, not aggregates

Workers report **what the model saw** — detections, enter/exit pairs, state labels,
per-frame counts — each tied to one moment, never a computed business metric. The
platform owns time: it derives dwell from `zone_enter`/`zone_exit` pairs, state
durations from consecutive `state_change` timestamps, and every chart from the raw
rows. This keeps numbers replayable, explainable, and independent of any one worker.
Concretely: **never post `zone_dwell`** (deprecated — stored but ignored) and never
put durations on `state_change` events.

## The contract

1. **Discover** — `list_sources`, `get_snapshot` (look at frames!), `get_store_map`,
   `list_zones`, `list_projection_surfaces`, and `list_zone_views`.
2. **Load the platform guide, then pick a recipe** — read `storelens-platform` first,
   then `list_skills()` → `get_skill(name)`. Skills live in `skills/`; follow the closest
   task playbook and compose them for multi-part requests.
3. **Register a job** — `register_job(name, description, source_ids, event_types)` *before*
   posting anything. Keep the returned `job_id`.
4. **Run analysis & post observations** — write a worker script (use `sdk/python/storelens.py`),
   run it, register its worker instance, heartbeat every 5–15 seconds, obey stop/restart
   flags, and `submit_events` in batches (≤5000; 100–500 is a good size, every 1–5 s).
5. **Verify & publish** — `get_events(job_id=...)` and `get_analytics(...)` to confirm the
   data renders, then `register_insight(...)` so the result appears as a card in the
   **Insights** tab (`list_insight_templates()` shows what fits the data; set honest
   `limitations` — they are displayed). Raw rows are browsable in the **Detections** tab.

## Event schema (the one thing to get right)

| field | meaning |
|---|---|
| `ts` | epoch seconds (float). Omit for "now". |
| `source_id` | which camera produced it |
| `event_type` | `detection` \| `zone_enter` \| `zone_exit` \| `transition` \| `state_change` \| `count` \| `custom` (\| `zone_dwell` — **deprecated**: stored, ignored by analytics/alerts) |
| `track_id` | stable per-object id (string) — required for occupancy/flow/dwell derivation |
| `point_px` | `{x,y}` representative pixel point. Feet/bbox-bottom-centre is the floor default. |
| `point_map` | `{x,y}` in map metres, if the worker already projected it |
| `bbox` | `[x,y,w,h]` pixels — preserved; bottom-centre is derived when `point_px` is absent |
| `keypoints` / `mask` | preserved pose or compressed segmentation evidence for review/ROI rules |
| `point_kind` | the point's meaning: feet, hip/torso centre, mask centroid, custom |
| `projection_surface_id` | named plane for a mattress/table/shelf/etc.; omit for floor |
| `zone_view_id` | explicit camera ROI provenance; normally the server auto-matches it |
| `zone_id` / `zone` | explicit zone (id or name) — otherwise auto-assigned from the map point |
| `value` | a per-frame count sample (`count` events only) — never a computed aggregate |
| `label` | the observed class for `detection` ("person"/"child"/"forklift"), the state for `state_change`, or what a `count` counts |
| `attributes` | free dict — e.g. `{"gender":"female"}`. Insights can group dwell by any attribute key. |

The stored row also records projection/assignment methods and the revisions of the
zone, floor calibration, projection surface, and zone view used at ingestion. Geometry
edits affect future rows; never silently reinterpret historical detections.

Conventions that make insights light up:
- **Heatmap** ← `detection` events with a point, ~1–2 per second per track is plenty.
- **Dwell** ← `zone_enter`/`zone_exit` pairs only; the platform derives durations
  (including in-progress visits, capped at 1 h). Close open visits with a `zone_exit`
  at shutdown.
- **Flow matrix** ← `zone_enter` events per track (or zoned detections as fallback).
- **State timeline** ← `state_change` with `label` on flips, plus one anchor at startup;
  durations and duration alerts are derived from consecutive timestamps per `source_id`.
- **Alerts** ← `create_alert_rule` (kinds: `dwell_exceeds`, `occupancy_exceeds`,
  `state_alert`, `event_match`) — dwell and state durations are platform-derived, and
  ongoing conditions (loiter without exit, door still open) also fire as events flow;
  `webhook_url` POSTs alert JSON to n8n/Zapier/anything.
- **Insights** ← `register_insight(title, block, dataset, params, ...)` — the Insights
  tab renders only registered definitions (blocks: metric, line, bar, table,
  heatmap_map, flow_matrix, state_timeline).

## Zones from camera views

Separate the physical zone from how one camera sees it:

1. `get_snapshot(source_id)` and `get_store_map()` — inspect the frame and the current
   global footprint. Confirm the footprint with the user before creating/updating it.
2. A **zone** is the canonical physical polygon in map metres. `polygon_px + source_id`
   is only a shortcut for points on the calibrated floor plane.
3. A **zone view** belongs to one zone and one camera. Store the visible outer polygon,
   an inset detection ROI, and one membership rule: `point`, `bbox_overlap`, or
   `keypoints_inside`. Use `unproject_points` to propose camera pixels from the map
   footprint, then check the result against the snapshot.
4. For a mattress, table, shelf, conveyor, or other elevated planar target, create a
   **projection surface** from at least four matching `{px,map}` points and attach it to
   the zone view. Height is metadata. Never subtract height from map Y; a homography
   maps one 2D plane to another and contains no vertical coordinate.
5. The `ztype` ("restricted", "queue", ...) remains only a semantic label. Alerts are
   separate platform configuration.
6. Workers post evidence (`bbox`, keypoints/mask, point and its `point_kind`). The server
   preserves that evidence, selects the relevant zone view/plane, projects, assigns the
   zone, and records all definition revisions.

For an uncalibrated camera, a zone view can still assign by pixel-space bbox/keypoints.
Post an explicit `zone_id` only when the server cannot perform that enrichment. Full
non-planar 3D localization requires camera intrinsics/extrinsics and is outside the
current plane-homography model.

## Working in this repo

- Server: `uvicorn server.app:app` → UI at http://localhost:8000, OpenAPI at `/docs`.
- Worker SDK: `sdk/python/storelens.py` (requests + optional OpenCV). Copy or import it.
- A job is metadata. A worker instance is heartbeat-backed runtime state. Dashboard
  stop/restart commands are cooperative; a deployment supervisor performs relaunch.
- Examples to crib from: `examples/` (simulator, motion-based heatmap tracker, dwell worker, fridge state worker).
- If OpenCV/ultralytics are unavailable, degrade gracefully: background-subtraction blobs
  (see `examples/heatmap_tracker.py`) still produce usable heatmaps and tracks.
- Zones missing? Propose them from `get_snapshot` frames and register with `create_zone`
  (see "Zones from camera views"), or ask the user to draw them in **Store Map**.
  Calibration missing? Ask the user to calibrate, or fall back to `point_map` you
  compute yourself.

## Skills index

| skill | use when the user asks for |
|---|---|
| `storelens-platform` | any StoreLens task; load first for platform purpose, API discovery, workflow, and safety defaults |
| `heatmap` | traffic/popularity heatmaps of the store or an area |
| `dwell-time` | how long people stay somewhere, optionally split by an attribute (gender, staff/customer…) |
| `state-monitoring` | open/closed, on/off states of equipment (fridge doors, lights) and their durations |
| `alerts-workflows` | notifications, thresholds, loitering, crowding, webhook/n8n integrations |
| `insights` | putting a result on the dashboard, pinning to Overview, curating cards |
