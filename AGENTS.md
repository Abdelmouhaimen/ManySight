# StoreLens — agent operating manual

You (Codex, or any coding agent) are the **analysis brain** of StoreLens. The platform is
deliberately dumb about computer vision: it stores camera sources, a floor plan with named
zones, per-camera homographies (camera pixels → floor meters), and a generic stream of raw
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

1. **Discover** — `list_sources`, `get_snapshot` (look at frames!), `get_store_map`, `list_zones`.
2. **Pick a recipe** — `list_skills()` → `get_skill(name)`. Skills live in `skills/` and are
   step-by-step playbooks with worker templates. Follow the closest one; compose them for
   multi-part requests ("dwell by gender at checkout" = dwell-time skill + an attribute model).
3. **Register a job** — `register_job(name, description, source_ids, event_types)` *before*
   posting anything. Keep the returned `job_id`.
4. **Run analysis & post observations** — write a worker script (use `sdk/python/storelens.py`),
   run it, and `submit_events` in batches (≤5000; 100–500 is a good size, every 1–5 s).
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
| `point_px` | `{x,y}` pixel position (person's feet = bottom-center of bbox). **Preferred**: the platform auto-projects it to map meters when the source is calibrated, then auto-assigns the containing zone. |
| `point_map` | `{x,y}` in floor meters, if you already projected |
| `bbox` | `[x,y,w,h]` pixels — used to derive the feet point if `point_px` absent |
| `zone_id` / `zone` | explicit zone (id or name) — otherwise auto-assigned from the map point |
| `value` | a per-frame count sample (`count` events only) — never a computed aggregate |
| `label` | the state name for `state_change` ("open"/"closed"), or what a `count` counts |
| `attributes` | free dict — e.g. `{"gender":"female"}`. Insights can group dwell by any attribute key. |

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

When the user describes an area by what's visible in a camera ("mark the dashed-line
area on cam 1 as a restricted zone"), you create the geometry — the platform owns what
it means:

1. `get_snapshot(source_id)` — look at the frame and propose a pixel polygon for the
   area. Show/describe it to the user for confirmation.
2. `create_zone(name, ztype, polygon_px=[...], source_id=...)` — the platform projects
   pixels → floor meters through the camera's homography (needs calibration; if the
   camera is uncalibrated, ask the user to calibrate in Store Map, or compute the map
   polygon yourself).
3. The `ztype` ("restricted", "queue", ...) is only a semantic label. Whether entering
   it should alert is a **separate** platform concern — e.g.
   `create_alert_rule("Restricted area entry", "event_match", {"event_type": "zone_enter", "zone_id": ...})`,
   created when the user asks for it.
4. Your worker stays ignorant of all this: it detects the objects the user asked for
   and posts `detection` / `zone_enter` / `zone_exit`. Enrichment auto-assigns zones
   from projected points, so a worker posting calibrated detections doesn't even need
   to know the zone exists.

Fallback for uncalibrated cameras: do zone membership in pixel space inside the worker
and post `zone_enter`/`zone_exit` with an explicit `zone_id` — ingestion accepts an
explicit zone without any map point.

## Working in this repo

- Server: `uvicorn server.app:app` → UI at http://localhost:8000, OpenAPI at `/docs`.
- Worker SDK: `sdk/python/storelens.py` (requests + optional OpenCV). Copy or import it.
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
| `heatmap` | traffic/popularity heatmaps of the store or an area |
| `dwell-time` | how long people stay somewhere, optionally split by an attribute (gender, staff/customer…) |
| `state-monitoring` | open/closed, on/off states of equipment (fridge doors, lights) and their durations |
| `alerts-workflows` | notifications, thresholds, loitering, crowding, webhook/n8n integrations |
| `insights` | putting a result on the dashboard, pinning to Overview, curating cards |
