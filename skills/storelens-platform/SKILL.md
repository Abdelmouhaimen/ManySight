---
name: storelens-platform
description: Load first for every StoreLens task. Explains platform boundaries, source security, observation samples, geometry, multiview, queries, dashboards, alerts, and worker lifecycle.
---

# StoreLens platform

StoreLens turns raw local camera/sensor observations into spatial and temporal state.
The rule is **observe locally, derive centrally**.

## Boundaries

Workers open sources, run models, track anonymous entities, and submit only schema-v2
`detection`, `measurement`, and `state`. StoreLens owns projection, canonical zone
assignment, visits, dwell, occupancy, transitions, state intervals, multiview association,
queries, dashboards, and alerts.

Never send `zone_id`/`zone` or worker-derived `zone_enter`, `zone_exit`, `zone_dwell`,
`state_change`, or `count` to the schema-v2 endpoint. Never call tracker IDs identified
people or invent cross-camera identity by joining similar IDs.

## Discover before changing

1. Call `get_platform_config` and use its endpoints.
2. Inspect sources, store map, zones, zone views, projection surfaces, calibrations,
   multiview groups, jobs/workers, current state, saved queries, dashboards, and alerts.
3. Reuse existing definitions where they match the request.
4. Load the closest task skill before implementing.

## Source security

Sources use `storelens_managed` protected connection fields or `external_secret` local
references. Ordinary discovery is redacted. Resolve a managed connection only for an
authorized local worker, keep it in memory, and never place credentials in observations,
fusion records, queries, dashboards, logs, code, or job metadata. StoreLens does not
proxy a feed or execute worker scripts.

The optional guided demo is explicitly different from a worker: it serves only a known
local sample-media allowlist and progressively replays a committed raw detection fixture.
It creates no worker row, uses `producer_kind=replay`, and must never be presented as
runtime inference. Its SQLite workspace is isolated until explicit setup promotion.

## Complete detection samples

For every processed detection frame, send zero or more detections plus exactly one
`detection_frame_count` measurement. All rows share source, entity type, one exact
timestamp, and one opaque `sample_id`. The count includes zero. Prefer the SDK sample
builder, which sends one immediate atomic batch.

StoreLens advances current state only after the marker count matches detections. Partial
samples, count mismatches, and duplicate markers do not replace current state. Legacy
rows without `sample_id` use exact source/timestamp fallback. Missing data affects
freshness; it never creates an observed zero.

## Geometry and multiview

Canonical zones are metric Polygon/MultiPolygon geometry. Camera zone views never mutate
them implicitly; `extend_zone_from_view` is the explicit, provenance-recorded union.
Rich calibration imports validate 3x4 projection matrices, metres, world axes, and
optional verification points before deriving the floor homography.

Only create a multiview group for compatible calibrated sources. Fusion consumes complete
samples and uses geometry/time/trajectory/topology with global assignment. Inspect member
evidence and `known`/`partial`/`unknown` quality. This is anonymous active-track
association, not biometric ReID.

## Publish data products

Preview deterministic queries through `query_data`; agents never receive SQL access.
Save one canonical question with `create_saved_query`. If requested, create a generated
dashboard and attach the query with a validated number/timeseries/bar/table/heatmap
presentation. Presentation changes do not duplicate the query.

Use a query-backed alert when the threshold should match a dashboard value. Alerts are
periodic, edge-triggered, and quality-aware. Unknown evidence does not false-clear; partial
quality requires explicit opt-in.

## Worker lifecycle

Register a job before posting. Register the actual worker instance, heartbeat every
5-15 seconds, and obey cooperative stop/restart. A deployment supervisor performs any
relaunch. Verify raw rows, source current samples, projections, fused member evidence,
and query output before publishing a dashboard or alert.

Load `source-onboarding`, `detection-tracking`, `measurement`, `state-observation`,
`geometry-calibration`, `multiview`, `analytics`, `generated-dashboard`, or
`alerts-workflows` as appropriate.

Do not reinitialize a space or its observations unless the user explicitly requests the
destructive action and chooses the history policy. Current queries default to the active
`space_revision_id`; never resolve a deleted zone reference by matching its old name.
