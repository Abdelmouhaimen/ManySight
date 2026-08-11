---
name: storelens-platform
description: General operating guide for StoreLens. Use first for every StoreLens request, especially when the user gives a short outcome-oriented prompt, the agent is connected only through MCP, or the task involves cameras, zones, workers, observations, analytics, or alerts. Explains the platform purpose, MCP versus REST responsibilities, API discovery, default workflow, the observation contract, validation, and safety boundaries before any task-specific skill is applied.
---

# StoreLens platform guide

Use this skill before any task-specific StoreLens playbook. Treat the live platform,
not prior conversation or demo assumptions, as the source of truth.

## Understand the platform

StoreLens is infrastructure for computer-vision analysis of physical spaces. It stores
logical source descriptors and encrypted managed credentials, a floor map, named global zones, floor calibration,
named projection planes, per-camera zone views/decision ROIs, analysis and worker-instance
registrations, raw observations, derived analytics, alerts, and saved analyses.

StoreLens does not choose a model or execute arbitrary CV scripts. It exposes worker
registration, heartbeat, staleness, and cooperative stop/restart commands; a deployment
supervisor still owns the actual process. Act as the analysis operator: understand the
question, inspect the configured space, choose or build a worker, run it where it can
reach the camera, and submit observations.

Keep the three surfaces distinct:

- **MCP** lets an agent discover and configure StoreLens and verify results.
- **REST API** is the stable interface workers use to register and submit data.
- **Dashboard** lets people configure the space and inspect observations, analytics, and
  reviewable signals.

A worker must not require MCP. MCP is an agent adapter, not a camera subscriber or
worker runtime.

Camera access is worker-local. StoreLens never opens or proxies a feed or captures a
snapshot. A source uses either `storelens_managed` or `external_secret`. Managed sources
keep structured non-secret connection fields separately from AES-GCM-encrypted credentials;
ordinary discovery never returns the credentials. An authorized worker may resolve them
with `get_source_connection`, using a dedicated credential-access key, and must keep the
result in memory. External sources resolve `locator.local_secret_ref` from an environment
variable, keychain, or ignored file on the worker device.

## Find the API

Start with `get_platform_config`. It resolves the editable `config/endpoints.json`
profile plus deployment overrides and is authoritative for the current connection.
If it is unavailable, use the StoreLens URL supplied by the MCP/client configuration
or by the user.

- Interactive OpenAPI: `{STORELENS_URL}/docs`
- REST base: `{STORELENS_URL}/api/v1`
- Health check: `{STORELENS_URL}/api/v1/health`
- Observation contract: `{STORELENS_URL}/api/v1/observations/contract` (or `get_observation_contract`)

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
   missing, call `create_source` with managed connection fields or a non-secret external
   reference. Inspect relevant frames
   directly on the worker device. Never invent source IDs, zones, camera coverage,
   placement, or calibration.
2. **Clarify the measurement.** State what an entity, count, visit, state, or alert will
   mean. Distinguish anonymous tracks from unique people and model output from fact.
3. **Load a recipe.** Call `list_skills` and then `get_skill` for the closest specialized
   playbook. Compose playbooks when necessary.
4. **Confirm geometry** (see the `geometry-calibration` skill for the full picture).
   Keep the global map footprint separate from its camera view. For a new zone, confirm
   the map footprint. For each camera, confirm the visible outer polygon and inset
   decision ROI. A zone creates no behavior by itself.
5. **Plan a bounded pilot.** Select the simplest adequate model, validation method,
   sampling rate, run duration, and stop condition. Reuse existing workers and saved
   analyses when appropriate.
6. **Register.** Call `register_job` before submitting anything and retain its `job_id`.
7. **Run and observe.** Run the worker outside the dashboard. Register a worker instance,
   heartbeat every 5–15 seconds, obey `should_stop`/`restart_requested`, and submit raw
   observations in batches through `submit_observations` (`POST /api/v1/observations/batch`).
8. **Verify.** Call `get_latest_observations()` and `query_analytics(...)`. Check
   timestamps, source attribution, stable entity IDs, projection, zone assignment,
   sampling rate, and obvious false positives.
9. **Publish only when useful.** Call `list_analysis_capabilities()` and `list_analyses()`
   before `create_analysis`. Avoid duplicates, state honest limitations in the `question`,
   and pin only when requested or clearly appropriate.
10. **Report operation honestly.** Report what ran, where it ran, job/model/version,
    observation counts, validation performed, limitations, and how to stop or restart it.

## Observe locally, derive centrally

A worker submits only three observation kinds — `detection`, `measurement`, `state` —
and StoreLens derives everything else: zones, visits, dwell, occupancy, movement, state
transitions and durations, every analysis, and every alert. Call
`get_observation_contract()` for the exact field-level contract; the common fields are:

| field | use |
|---|---|
| `schema_version` | `2` |
| `observation_id` | worker-generated idempotency key — retries are safe |
| `kind` | `detection` \| `measurement` \| `state` |
| `timestamp` | observation time (epoch seconds or ISO-8601) |
| `source_id` | camera/sensor that produced the observation |
| `entity_id` | opaque per-track id (never a verified human identity) |
| `identity_scope` | `worker_run` (default) \| `source` \| `workspace` |
| `attributes` | free model/domain metadata — becomes an Analytics split dimension automatically |

Do not send top-level fields outside this contract. A worker must never send `zone_id`/
`zone`, and never submit the legacy derived kinds `zone_enter`/`zone_exit`/`zone_dwell`/
`state_change`/`count` — `submit_observations` rejects those with a
`legacy_derived_observation` error. See `detection-tracking`, `measurement`, and
`state-observation` for kind-specific fields and worker templates.

Ingestion records `projection_method`, `zone_assignment_method`, and the zone,
calibration, surface, and zone-view revisions. This provenance appears in the
Observations tab. Editing geometry affects future rows only; never rewrite historical
evidence silently.

## Build workers conservatively

- Prefer a lightweight, supported model that directly measures the requested concept.
- Keep a worker and its virtual environment in the user-designated workspace or edge gateway.
- Keep resolved connection material in memory and never print camera credentials or API
  keys. Use `StoreLens.open_capture(source)`, which prefers an explicit local override,
  then managed resolution, then an external reference.
- Use anonymous tracking and stable per-run `entity_id`s when tracking is required.
- Sample detections around 1-2 Hz per entity unless the measurement needs another rate.
- Batch submissions, handle disconnects, retry with bounds, and flush on shutdown.
- Call `register_worker` after the process actually starts. Heartbeat every 5–15 seconds,
  include useful metrics such as FPS/queue depth, and exit cleanly when instructed.
- Start with a short run and explicit stop condition. Do not run indefinitely unless
  the user asks for continuous operation.
- Record model name/version and useful validation metadata in `attributes`; use `confidence`
  for model confidence.
- Degrade explicitly when dependencies or camera access are unavailable; do not pretend
  that a fallback measures the same concept with equal accuracy.

Remember that job status is registration metadata. `latest_worker.effective_status`
is heartbeat-backed, but restart still requires a process supervisor. Source health is
derived from observation ingestion and heartbeats, not from a platform camera probe.

## Protect meaning, privacy, and trust

- Avoid identification and sensitive-trait inference unless explicitly authorized and
  appropriate for the deployment.
- Describe entities as tracks, not confirmed unique people. Never store biometric
  embeddings, face templates, or raw re-identification vectors — `entity_id` is an opaque
  worker-provided identifier, and StoreLens never joins similar IDs or visual attributes
  to invent cross-camera identity.
- Explain geometry limitations. Feet plus floor calibration is appropriate for standing
  floor traffic. Sitting, lying, occluded, or elevated subjects need an appropriate
  representative point and usually a camera zone view; planar elevated targets need a
  named surface. Arbitrary 3D localization needs camera intrinsics/extrinsics and is not
  solved by a 2D homography.
- Confirm consequential alerts, webhooks, or other external actions before creating
  them unless the user explicitly requested them.
- Keep observations traceable in the Observations tab and present derived conclusions as
  model estimates with limitations.

## Choose specialized playbooks

After this guide, load the closest available skill:

- `detection-tracking` for people/object positions, spatial activity, and time in zones.
- `measurement` for a numeric reading over time (counts, queue length, any classifier output).
- `state-observation` for equipment or scene-state monitoring.
- `geometry-calibration` for zones, zone views, projection surfaces, and calibration.
- `alerts-workflows` for thresholds, review signals, and webhooks.
- `analytics` for publishing verified results to the dashboard as a saved analysis.

If no specialized skill fits, use OpenAPI and the raw-observation contract as the
boundary. Implement the narrowest reversible pilot, verify it, and document the new
pattern before treating it as reusable.
