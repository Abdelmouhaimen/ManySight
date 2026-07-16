# StoreLens — agent operating manual

You (Codex, or any coding agent) are the **analysis brain** of StoreLens. The platform is
deliberately dumb about computer vision: it stores camera sources, a floor plan with named
zones, per-camera homographies (camera pixels → floor meters), and a generic event stream
it renders as insights (heatmaps, dwell, flow, states, alerts). **You** pick the models,
write the worker scripts, run them, and post events back.

## The contract

1. **Discover** — `list_sources`, `get_snapshot` (look at frames!), `get_store_map`, `list_zones`.
2. **Pick a recipe** — `list_skills()` → `get_skill(name)`. Skills live in `skills/` and are
   step-by-step playbooks with worker templates. Follow the closest one; compose them for
   multi-part requests ("dwell by gender at checkout" = dwell-time skill + an attribute model).
3. **Register a job** — `register_job(name, description, source_ids, event_types)` *before*
   posting anything. Keep the returned `job_id`.
4. **Run analysis & post events** — write a worker script (use `sdk/python/storelens.py`),
   run it, and `submit_events` in batches (≤5000; 100–500 is a good size, every 1–5 s).
5. **Verify** — `get_events(job_id=...)` and `get_analytics(...)` to confirm the insights
   render; tell the user to open the **Insights** tab.

## Event schema (the one thing to get right)

| field | meaning |
|---|---|
| `ts` | epoch seconds (float). Omit for "now". |
| `source_id` | which camera produced it |
| `event_type` | `detection` \| `zone_enter` \| `zone_exit` \| `zone_dwell` \| `transition` \| `state_change` \| `count` \| `custom` |
| `track_id` | stable per-object id (string) — required for occupancy/flow/dwell derivation |
| `point_px` | `{x,y}` pixel position (person's feet = bottom-center of bbox). **Preferred**: the platform auto-projects it to map meters when the source is calibrated, then auto-assigns the containing zone. |
| `point_map` | `{x,y}` in floor meters, if you already projected |
| `bbox` | `[x,y,w,h]` pixels — used to derive the feet point if `point_px` absent |
| `zone_id` / `zone` | explicit zone (id or name) — otherwise auto-assigned from the map point |
| `value` | the number: dwell seconds, count, duration… |
| `label` | the state name for `state_change` ("open"/"closed") |
| `attributes` | free dict — e.g. `{"gender":"female"}`. Insights can group dwell by any attribute key. |

Conventions that make insights light up:
- **Heatmap** ← `detection` events with a point, ~1–2 per second per track is plenty.
- **Dwell** ← explicit `zone_dwell` with `value`=seconds on exit (best), or `zone_enter`/`zone_exit` pairs (platform derives).
- **Flow matrix** ← `zone_enter` events per track (or zoned detections as fallback).
- **State timeline** ← `state_change` with `label`; on a change, set `value` = the finished state's duration and `attributes.prev_label` = the state that just ended (enables duration alerts).
- **Alerts** ← `create_alert_rule` (kinds: `dwell_exceeds`, `occupancy_exceeds`, `state_alert`, `event_match`); `webhook_url` POSTs alert JSON to n8n/Zapier/anything.

## Working in this repo

- Server: `uvicorn server.app:app` → UI at http://localhost:8000, OpenAPI at `/docs`.
- Worker SDK: `sdk/python/storelens.py` (requests + optional OpenCV). Copy or import it.
- Examples to crib from: `examples/` (simulator, motion-based heatmap tracker, dwell worker, fridge state worker).
- If OpenCV/ultralytics are unavailable, degrade gracefully: background-subtraction blobs
  (see `examples/heatmap_tracker.py`) still produce usable heatmaps and tracks.
- Zones/calibration missing? Ask the user to draw them in **Store Map**, or (calibration only)
  fall back to `point_map` you compute yourself. Look at `get_snapshot` frames and propose
  zone polygons if the user wants automatic naming.

## Skills index

| skill | use when the user asks for |
|---|---|
| `heatmap` | traffic/popularity heatmaps of the store or an area |
| `dwell-time` | how long people stay somewhere, optionally split by an attribute (gender, staff/customer…) |
| `state-monitoring` | open/closed, on/off states of equipment (fridge doors, lights) and their durations |
| `alerts-workflows` | notifications, thresholds, loitering, crowding, webhook/n8n integrations |
