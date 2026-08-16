---
name: manysight-core
description: Load first for every ManySight task. The invariants that decide whether an implementation is correct — platform boundary, atomic samples, identity, geometry, quality, and where authority lives.
---

# ManySight core

ManySight turns observations produced by **local** workers into spatial and temporal state
for a physical space. One rule decides most design questions:

> **Observe locally, derive centrally.**

Start every task with `inspect_workspace()`. Then `list_workflows()` and
`get_workflow(name)` for the job you were asked to do. Do not rediscover the architecture
by trying tools.

## The boundary

A worker opens sources, runs models, tracks anonymous entities, and submits only raw
perception evidence:

| kind | meaning |
|---|---|
| `detection` | an observed entity with pixel evidence |
| `measurement` | an observed number |
| `state` | an observed current categorical value |

ManySight owns everything derived: projection, canonical zone assignment, visits, dwell,
occupancy, transitions, state intervals, multiview fusion, saved queries, dashboards, and
alerts.

A worker must never submit `zone_id`/`zone`, `zone_enter`, `zone_exit`, `zone_dwell`,
`state_change`, `count`, dwell, occupancy, visits, transitions, or fused identity. The
current ingestion path rejects those kinds outright.

## Atomic samples, and what zero means

One successfully processed camera frame is one `DetectionSample` containing 0..N
detections:

- `detections=[]` is a **known explicit zero** and must be submitted.
- **No completed fresh sample means unknown or stale — not zero.**
- Never fake a zero-confidence detection to represent an empty frame.
- Elapsed wall-clock time never fabricates an empty scene: scene contents persist until a
  newer complete sample arrives, and freshness is reported separately.

## Identity

`entity_id` is an opaque **source-local tracker ID**. It is not a person, not verified,
and not comparable across sources. Fused multiview IDs are anonymous physical-track
estimates derived from geometry, time, and topology — not identity and not appearance
ReID. Never join tracker IDs across cameras yourself.

## Geometry

A **ZoneView** is one camera's pixel polygon. A **Zone** is the single canonical physical
footprint in map metres. One physical region is one canonical zone, never one per camera.
Creating or editing a view never changes canonical geometry; extension is explicit and
records provenance.

## Quality

`known`, `partial`, and `unknown` are three different answers and must not be collapsed.
A required camera going stale does not mean the zone is empty.

## Where authority lives

The current MCP tools, `GET /api/v1/observations/contract`, `get_worker_recipe()`,
`/openapi.json`, and these skills are authoritative.

**Do not treat an arbitrary repository script as ManySight protocol documentation.** An
example, a demo worker, or an older file on disk may predate the current API. This is the
single most common way an otherwise capable agent gets ManySight wrong.

Agents never receive raw SQL and never generate dashboard code.

## Safety

Sources use `manysight_managed` encrypted connection material or an `external_secret`
local reference. Ordinary discovery is redacted. Resolve a connection only inside the
authorized local worker, keep it in memory, and never place it in observations, fused
state, queries, dashboards, logs, code, or job metadata. ManySight does not proxy feeds
and does not execute worker scripts.

Space and observation reinitialization are destructive, exact-confirmation operations.
Never invoke them without an explicit user request. Queries default to the active
`space_revision_id`; never resolve a deleted zone reference by matching its old name.

## Next

| Skill | Use |
|---|---|
| [`sources-and-cameras`](../sources-and-cameras/SKILL.md) | onboarding, credentials, inspecting a camera view |
| [`geometry-and-zones`](../geometry-and-zones/SKILL.md) | canonical zones, views, surfaces, calibration |
| [`perception-workers`](../perception-workers/SKILL.md) | detection/measurement/state workers |
| [`multiview-fusion`](../multiview-fusion/SKILL.md) | calibrated groups and fused state |
| [`queries-dashboards-alerts`](../queries-dashboards-alerts/SKILL.md) | deterministic questions, views, thresholds |
| [`guided-demo`](../guided-demo/SKILL.md) | the isolated playable demo |
