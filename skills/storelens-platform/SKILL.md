---
name: storelens-platform
description: General operating guide for StoreLens. Use first for every StoreLens request, especially when the user gives a short outcome-oriented prompt, the agent is connected only through MCP, or the task involves cameras, zones, workers, observations, analytics, alerts, or insights. Explains the platform purpose, MCP versus REST responsibilities, API discovery, default workflow, event contract, validation, and safety boundaries before any task-specific skill is applied.
---

# StoreLens platform guide

Use this skill before any task-specific StoreLens playbook. Treat the live platform,
not prior conversation or demo assumptions, as the source of truth.

## Understand the platform

StoreLens is infrastructure for computer-vision analysis of physical spaces. It stores
camera access, snapshots, a floor map, named zones, camera placement and pixel-to-map
calibration, analysis registrations, raw observations, derived analytics, alerts, and
structured insight definitions.

StoreLens does not choose a model or supervise a persistent CV process. Act as the
analysis operator: understand the user's question, inspect the configured space, choose
or build a worker, run it where it can reach the camera, and submit observations.

Keep the three surfaces distinct:

- **MCP** lets an agent discover and configure StoreLens and verify results.
- **REST API** is the stable interface workers use to register and submit data.
- **Dashboard** lets people configure the space and inspect detections, insights, and
  reviewable signals.

A worker must not require MCP. MCP is an agent adapter, not a camera subscriber or
worker runtime.

## Find the API

Use the StoreLens URL supplied by the MCP/client configuration or by the user. The
default local URL is `http://localhost:8000`.

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

1. **Discover.** Call `list_sources`, `get_store_map`, and `list_zones`. Inspect relevant
   frames with `refresh_snapshot` and `get_snapshot`. Never invent source IDs, zones,
   camera coverage, placement, or calibration.
2. **Clarify the measurement.** State what an object, count, visit, state, or alert will
   mean. Distinguish anonymous tracks from unique people and model output from fact.
3. **Load a recipe.** Call `list_skills` and then `get_skill` for the closest specialized
   playbook. Compose playbooks when necessary.
4. **Confirm geometry.** For a new camera-view zone, propose a pixel polygon after
   inspecting a snapshot, explain its coverage, and obtain user confirmation before
   `create_zone`. A zone is geometry plus a label; it creates no behavior by itself.
5. **Plan a bounded pilot.** Select the simplest adequate model, validation method,
   sampling rate, run duration, and stop condition. Reuse existing workers and insight
   definitions when appropriate.
6. **Register.** Call `register_job` before submitting anything and retain its `job_id`.
7. **Run and observe.** Run the worker outside the dashboard. Post raw observations in
   batches through `POST /api/v1/events` or `submit_events`.
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
| `point_px` | camera-pixel location; for a person use bottom-center of the box |
| `point_map` | floor-map metres when already projected |
| `bbox` | pixel bounding box accepted for position enrichment |
| `zone_id` / `zone` | explicit zone; otherwise let calibrated projection assign it |
| `value` | raw numeric sample, such as a per-frame count |
| `label` | observed class or state |
| `attributes` | free model/domain metadata such as confidence, model version, or product ID |

Put domain-specific fields in `attributes`; arbitrary top-level fields are not part of
the contract. Do not assume an attribute automatically becomes a filter or insight.

Examples of the derive-only rule:

- Post person positions, not a heatmap.
- Post `zone_enter` and `zone_exit`, not calculated dwell. `zone_dwell` is deprecated.
- Post label-only `state_change` flips, not state durations.
- Post per-frame `count` samples, not cumulative totals.

The platform derives heatmaps, occupancy, dwell, flow, state duration, alerts, and
registered insight views from these observations.

## Build workers conservatively

- Prefer a lightweight, supported model that directly measures the requested concept.
- Keep a worker and its virtual environment in the user-designated workspace.
- Store configuration in environment variables or ignored local files; never print
  camera credentials or API keys.
- Use anonymous tracking and stable per-run IDs when tracking is required.
- Sample detections around 1-2 Hz per track unless the measurement needs another rate.
- Batch submissions, handle disconnects, retry with bounds, and flush on shutdown.
- Start with a short run and explicit stop condition. Do not run indefinitely unless
  the user asks for continuous operation.
- Record model name/version, confidence, and useful validation metadata in `attributes`.
- Degrade explicitly when dependencies or camera access are unavailable; do not pretend
  that a fallback measures the same concept with equal accuracy.

Remember that job status is registration metadata, not proof of a living process, and
camera online/offline status is the last snapshot test, not continuous health.

## Protect meaning, privacy, and trust

- Avoid identification and sensitive-trait inference unless explicitly authorized and
  appropriate for the deployment.
- Describe tracks as tracks, not confirmed unique people.
- Explain floor-plane limitations: a feet projection can fail for sitting, lying,
  occluded, or elevated subjects.
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
