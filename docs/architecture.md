# Architecture and current scope

StoreLens separates local observation from central spatial and temporal derivation.
The platform does not need to know which detector, tracker, classifier, or sensor
produced an observation.

## Components

1. **Sources** describe logical cameras, files, streams, or sensors. Connection
   configuration is either StoreLens-managed or resolved from an external secret.
2. **Local workers** open sources, run inference and tracking, and submit direct
   observations through the REST API or Python SDK.
3. **The StoreLens API** persists observations in SQLite and enriches spatial evidence
   using the geometry active at ingestion.
4. **The current-state service** commits only complete processed samples and maintains
   bounded source-local scene state.
5. **The multiview service** associates source-local tracks inside explicit groups of
   cameras that share compatible metric world geometry.
6. **The ManySight dashboard** is the bundled interface for setup, observations,
   fused/source-debug Live views, generated dashboards, workers, and alerts.
7. **The MCP server** exposes safe platform operations and agent playbooks. It is not
   a worker runtime and never receives camera credentials through ordinary discovery.
8. **The optional guided demo** routes an explicit browser session to a temporary
   SQLite workspace. Its offline generator derives a versioned raw `DetectionSample`
   fixture through the real platform and commits a provenance-hashed replay cache.
   Runtime advances one master media clock and performs no live fusion or inference.

## Data flow

Workers submit schema-v2 `detection`, `measurement`, or `state` observations. A
detection may carry a pixel point, bounding box, keypoints, or mask. StoreLens chooses
a representative point, applies the relevant floor or named-plane homography, assigns
a physical zone, and records the geometry revisions used at ingestion.

StoreLens derives current presence, density, visits, dwell, transitions, measurement
series, state intervals, fused occupancy, saved-query results, generated dashboards,
and alerts. Legacy `/api/v1/events` remains accepted for compatibility. New camera
workers should use `/api/v1/detection-samples`; other producers use
`/api/v1/observations/batch`.

## Complete source samples

Continuous detection workers submit one atomic `DetectionSample` with one source-local
`sample_id`, one exact timestamp, and zero or more detections. `detections=[]` commits
an observed empty frame without a fake detection. The SDK builder accumulates detections
in memory and submits that same public envelope.

StoreLens internally normalizes the envelope for its existing event/materialization
model. Legacy detection rows plus one matching `detection_frame_count` measurement are
still supported, but partial or count-mismatched legacy samples never replace current
state. Older rows without `sample_id` retain exact source/timestamp fallback semantics.

Scene contents and freshness are independent. If a worker stops, StoreLens retains the
last complete sample and marks it stale; elapsed wall time never fabricates an empty
scene.

## Multiview association

A multiview group explicitly lists calibrated sources in one world frame. Fusion is
downstream of complete source samples. Candidate associations are gated by time,
metric distance, short trajectory prediction, and optional camera topology, then
resolved with global minimum-cost assignment. Source observations and tracker IDs are
never rewritten. Fused records retain their contributing source event, local track,
sample, algorithm version, and configuration revision.

Fused identities mean anonymous active-track association, not verified people. The
current implementation uses no appearance model, face embedding, or biometric identity.
Quality is `known`, `partial`, or `unknown` according to source freshness. Stale sources
do not manufacture a zero count.

## Geometry model

The map and zones use metres. A canonical zone is GeoJSON Polygon or MultiPolygon;
disconnected components remain disconnected. A zone view stores how one camera sees a
zone and never changes the canonical footprint implicitly. An explicit extension
operation projects and unions a chosen view polygon while recording calibration,
surface, view, source-pixel, projected-map, and resulting-zone revisions.

A source may use a four-point floor homography or a rich 3x4 world-to-pixel calibration.
The importer validates metric units, explicit world axes, matrix rank, optional
distortion/intrinsic/extrinsic metadata, and verification points, then derives the
ground-plane homography used by normal enrichment. Named projection surfaces model
other planes such as shelves or tables. A homography does not reconstruct arbitrary 3D.

## Queries, dashboards, and alerts

Agents compose validated query definitions, not SQL. Saved queries contain subject,
measures, filters, grouping, range, and comparison. Dashboard widgets reference those
queries and select one safe presentation: number, timeseries, bar, table, or heatmap.
Deleting a dashboard preserves its queries and all observations.

Query-backed alerts evaluate the same deterministic state. They are edge-triggered,
respect cooldowns, do not repeatedly fire while a condition remains true, and do not
false-clear on unknown evidence. Partial quality is usable only when a rule explicitly
allows it.

## Current operational scope

- Persistence uses SQLite and one workspace/store record.
- API-key authentication is optional and is not a user/account or role system.
- Managed credentials require operator-provided encryption and resolution keys.
- Worker stop/restart is cooperative; an external supervisor performs relaunch.
- Source reachability is evaluated on the worker machine.
- Accuracy depends on models, calibration, synchronization, sampling, and tracking.
- Multiview fusion is geometry-first active-track association, not long-term ReID.

These limits are not production-readiness claims. See [Development](development.md),
[Workers](workers.md), [Geometry](geometry.md), and [Multiview](multiview.md).

## Workspace isolation and revisions

Normal requests use the configured workspace database. A guided-demo request carries an
opaque session identifier and is routed through an async-task-local database context;
session lifecycle endpoints remain in the normal registry. Temporary paths are never
returned publicly. Discard removes the isolated database, while promotion copies a
strict setup allowlist in one normal-workspace transaction.

Every raw observation records the current `space_revision_id`. Starting a new mapped
space archives a geometry snapshot and advances that ID. Unified queries default to the
current revision, so old geometry evidence cannot contaminate current state. See
[Workspace reinitialization](workspace-reinitialization.md).
