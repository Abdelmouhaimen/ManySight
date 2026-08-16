# StoreLens agent operating manual

StoreLens follows one rule: **observe locally, derive centrally**.

## How to operate StoreLens

Four steps, in this order. Do not rediscover the architecture by trying tools.

1. **`inspect_workspace()`** — one snapshot of sources, calibration, zones, perception
   freshness, multiview groups, saved queries, dashboards, alert rules, and readiness.
2. **`list_workflows()` then `get_workflow(name)`** — route the job you were asked to do
   to its prerequisites, sequence, invariants, and tools.
3. **`get_skill(name)`** — the full playbook behind a step, starting with
   [`storelens-core`](skills/storelens-core/SKILL.md).
4. **Verify with real reads** — `inspect_perception`, `run_query`, and the observation
   endpoints. Never claim a worker is healthy or observations are flowing without checking.

Three interfaces, three jobs:

| interface | role |
|---|---|
| REST + SDK | the complete low-level platform interface (`/openapi.json` is authoritative) |
| MCP | a curated semantic surface for coding agents — 18 tools, not 60 |
| skills | the workflow knowledge behind those tools |

See [docs/agent-surface.md](docs/agent-surface.md) for the exact tool list and the
legacy/compatibility strategy.

## Platform boundary

Local workers open sources, run models, track anonymous entities, and submit only
schema-v2 `detection`, `measurement`, or `state`. StoreLens owns projection, canonical
zone assignment, visits, dwell, occupancy, transitions, state intervals, multiview
association, saved queries, dashboards, and alerts.

Workers must never submit `zone_id`/`zone`, `zone_enter`, `zone_exit`, `zone_dwell`,
`state_change`, `count`, or calculated analytics. `entity_id` is an opaque source-local
tracker ID, not a verified person identity. Do not invent cross-camera identity by
joining IDs or attributes.

StoreLens does not proxy feeds, run models, or execute worker scripts. Camera access and
inference are local to the agent or worker; `plan_frame_capture` returns a plan to run
yourself, never image bytes.

## Authority

`get_worker_recipe()`, `GET /api/v1/observations/contract`, `/openapi.json`, the current
MCP tools, and `skills/` are authoritative.

**Do not treat an arbitrary repository script as StoreLens protocol documentation.** An
example, a demo worker, or an older file on disk may predate the current API. If a file
disagrees with the recipe, the recipe is right.

## Source access

A source may use encrypted `storelens_managed` connection material or an
`external_secret` local reference. Ordinary discovery is redacted. Resolve credentials
only in an explicitly authorized local worker, use them in memory, and never place them
in observations, fused state, queries, dashboards, logs, code, or job metadata.

The guided demo is a narrow exception for bundled local sample media: it serves an
allowlisted NVIDIA asset set to the browser, replays a versioned numerical fixture with
`producer_kind=replay`, and creates no worker heartbeat. Never describe replay as live
inference. Demo workspaces are isolated; promote only the explicit setup allowlist.

## Observation envelope

Common fields are `schema_version: 2`, idempotent `observation_id`, `kind`, `timestamp`,
`source_id`, optional `sample_id`, worker/job IDs, confidence, opaque `entity_id`, honest
`identity_scope`, and attributes.

- `detection`: `entity_type`, optional label, and pixel evidence (`point_px`, corner-form
  `bbox_px`, keypoints, mask). StoreLens chooses explicit point, feet/ankles, then bbox
  bottom-center. Trusted non-camera producers may use `point_map`.
- `measurement`: name, numeric value, `gauge|delta|cumulative`, optional unit and label.
- `state`: name and observed label on every reading, including repeats.

For every processed detection frame, submit one atomic `DetectionSample` containing zero
or more detections to `POST /api/v1/detection-samples`. `detections=[]` is a complete
observed zero. Prefer the SDK sample builder. Legacy detection rows plus a matching
`detection_frame_count` marker remain compatible but are not the contract for new
workers; partial or count-mismatched samples do not replace current state. **Missing data
changes freshness, never scene value: no fresh complete sample means unknown, not zero.**

A successful response means the sample is durably stored, along with the submitting
source's current state. Cross-camera fusion is scheduled separately and runs at most every
10 ms from each source's freshest sample, so a worker submitting faster than that may find
several of its frames represented in history but only the newest in the combined view.
Those are **coalesced live updates**, not dropped observations: every accepted sample stays
queryable. Reads of fused state are never stale — they run any pending fusion first.
Submitting at full camera rate is supported; local detection may still run faster than
central submission.

## Geometry and multiview

Canonical zones are metric GeoJSON Polygon/MultiPolygon. Zone views are camera-specific
pixel polygons and never mutate canonical geometry implicitly. One physical region is ONE
canonical zone, never one per camera.

When a named region has no geometry, inspect the calibrated cameras (`plan_frame_capture`)
before asking the user for coordinates, `preview_zone` to project without persisting, get
approval, then `commit_zone`. Subjective geometry is never persisted before approval.

Rich calibration imports accept validated 3x4 world-to-pixel matrices with metres,
explicit world axes, ground-plane Z, and optional camera metadata/verification points.
Only group compatible calibrated sources. Multiview consumes complete source samples and
uses geometry/time/trajectory/topology plus global assignment. It preserves source
evidence and produces anonymous active fused tracks with `known|partial|unknown` quality;
it is not biometric ReID. Cross-camera occupancy uses fused entities, never a count of
raw local track IDs.

## Analytics

The saved query computes; a dashboard only presents. Presentation changes never duplicate
a query. Agents never receive raw SQL and never generate dashboard code.

Threshold words are exact and are never normalized: "more than 2" is `> 2`, "at least 2"
is `>= 2`, "fewer than 3" is `< 3`, "at most 3" is `<= 3`, "exactly 3" is `== 3`. Ask
rather than guess an unlisted phrasing.

Alerts are periodic, edge-triggered, cooldown-aware, and quality-aware: unknown evidence
does not false-clear, and `partial` requires explicit `allow_partial`. An alert must never
infer zero because a required camera went stale.

## Skills

| Skill | Use |
|---|---|
| [`storelens-core`](skills/storelens-core/SKILL.md) | **load first** — invariants and where authority lives |
| [`sources-and-cameras`](skills/sources-and-cameras/SKILL.md) | onboarding, credentials, inspecting a camera view |
| [`geometry-and-zones`](skills/geometry-and-zones/SKILL.md) | zones, views, surfaces, preview→approve→commit |
| [`perception-workers`](skills/perception-workers/SKILL.md) | detection, measurement, and state workers |
| [`multiview-fusion`](skills/multiview-fusion/SKILL.md) | calibrated groups and fused state |
| [`queries-dashboards-alerts`](skills/queries-dashboards-alerts/SKILL.md) | questions, views, exact thresholds |
| [`guided-demo`](skills/guided-demo/SKILL.md) | the isolated playable demo and its boundaries |

Space and observation reinitialization are destructive, exact-confirmation operations.
Never invoke them without an explicit user request. A retained historical observation
belongs to its recorded `space_revision_id`; deleted-zone query references stay
unresolved and must not be matched by name.
