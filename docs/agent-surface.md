# The agent operating surface

ManySight has three interfaces with three different jobs. Conflating them is what makes
coding agents operate the platform badly.

```text
REST + SDK   complete low-level platform interface     /openapi.json is authoritative
MCP          curated semantic interface for agents     19 tools, shaped for a context window
skills       the workflow knowledge behind the tools   skills/*/SKILL.md
evaluation   proof the correct path is discoverable    evals/agent_operability/
```

REST and the SDK are deliberately **not** reduced. Curation applies to MCP only: an agent
that must choose between sixty low-level tools reconstructs the architecture by trial and
error, which is exactly how zones get created per camera, thresholds get rounded to the
nearest operator, and stale example scripts get mistaken for the protocol.

## The curated MCP tools

Nineteen tools, grouped by job:

| group | tools |
|---|---|
| context and workflows | `inspect_workspace`, `list_workflows`, `get_workflow`, `get_skill` |
| sources and cameras | `inspect_source`, `configure_source`, `get_source_connection`, `plan_frame_capture` |
| geometry and zones | `preview_zone`, `commit_zone` |
| perception | `inspect_perception`, `get_worker_recipe`, `request_worker_state` |
| multiview | `configure_multiview_group` |
| analytics | `run_query`, `configure_saved_query`, `configure_dashboard`, `configure_alert` |
| destructive workspace operations | `reset_cameras` |

Each is backed by a real REST endpoint, because the MCP server holds no business logic:

| tool | endpoint |
|---|---|
| `inspect_workspace` | `GET /api/v1/agent/workspace` |
| `list_workflows` / `get_workflow` | `GET /api/v1/agent/workflows[/{name}]` |
| `inspect_source` | `GET /api/v1/agent/sources/{id}` |
| `plan_frame_capture` | `GET /api/v1/agent/sources/{id}/frame-capture-plan` |
| `inspect_perception` | `GET /api/v1/agent/perception` |
| `get_worker_recipe` | `GET /api/v1/agent/worker-recipe` |
| `preview_zone` | `POST /api/v1/agent/zone-preview` |
| `commit_zone` | `POST /api/v1/agent/zone-commit` |
| `reset_cameras` | `POST /api/v1/workspace/reset-cameras` |

The rest map onto existing source, multiview, query, dashboard, and alert-rule routes.
`get_skill` reads `skills/` from disk and prefixes the deployment's resolved endpoints.

## `inspect_workspace` — the authoritative first call

One response, six sections, no credentials:

- **workspace** — name, space type, environment, dimensions, map readiness, current
  `space_revision_id` and revision number.
- **sources** — per source: configured, connection management, credential status
  (`stored`/`absent`, never the material), placed, calibrated, calibration revision, frame
  size, projection-surface and zone-view counts, observation state and sample age.
- **geometry** — map readiness, calibrated and uncalibrated source IDs, projection-surface
  count, and each zone with its type, revision, geometry type, component count, and the
  sources that carry a view of it.
- **perception** — the entity types with complete samples, which sources have ever
  produced one, which are fresh, and the freshness threshold.
- **multiview** — per group: members, calibrated members, fresh and stale members,
  `known|partial|unknown` quality, fused entity count, gates, configuration revision.
- **analytics** — saved queries with subject/measures/filters, dashboards with widget
  counts, alert rules with kind/condition/last fired, and a trimmed query-capability block.
- **readiness** and **next_steps** — `ready|partial|missing` per dimension plus short,
  state-derived hints such as pointing a missing zone at the
  `define-zone-from-cameras` workflow.

Everything comes from a materialized read model or a bounded query, and every list is
capped, so the call stays cheap on a workspace with millions of observations.

## `inspect_perception` — reuse before starting anything

Answers "can ManySight already answer this?" for an entity type and a set of sources, and
returns a single `action`:

| action | meaning |
|---|---|
| `reuse` | healthy perception already covers every requested source — do not start a worker |
| `extend_coverage` | some sources are covered; add the missing ones |
| `restart_or_repair` | samples exist but are stale |
| `perception_missing` | no complete sample has ever arrived |

Per source it reports state, freshness, last detection count, observed submission rate, the
`source_fps`/`processing_fps`/`device` the worker reports in heartbeat metrics, whether
detections carry a source-local `entity_id`, which spatial evidence they carry, and the
latest worker heartbeat. It also reports multiview readiness and any compatible existing
job.

`performance` is a **separate axis from availability**. A worker tracking at 4 FPS is
producing real observations, so it stays `healthy`; the rate shows up as `below_target`
with the likely causes, and as a reason in the response. That distinction is the point:
occasional arriving samples must not be read as a healthy tracking rate, and a slow worker
must not be read as missing perception. `readiness_axes` names the three independent
questions — camera available, perception runnable, performance capable — so a missing GPU
is never reported as an unusable camera.

There is no capability registry table. Everything is derived from existing source, job,
worker, and observation records, so capability status cannot drift away from reality — and
this milestone required no schema migration.

Two semantics matter:

- A **complete empty sample** is an observed zero, not a missing capability. Tracking and
  geometry facts are `null` (not demonstrated by this sample) rather than `false`, so a
  camera correctly reporting an empty aisle stays `healthy`.
- A **stale or absent** source is `unknown`, never zero.

## `get_worker_recipe` — the current contract, from the running platform

Returns the preferred endpoint and envelope (with its field list read from the live
`DetectionSample` model), empty-frame semantics, identity rules, spatial point meaning,
forbidden worker output, the rate plan, acceleration and environment guidance, lifecycle
and heartbeat expectations, the managed-connection workflow, multiview prerequisites, the
SDK helper, and how to verify.

It exists because an agent that finds an old demo worker on disk will otherwise treat that
file as the protocol. The recipe is generated at request time, so it cannot fall behind.

`sampling` separates three rates that are easy to conflate — source FPS, local processing
FPS, and central submission Hz — and computes a plan for each requested source from what is
actually known about it (`source_fps` from the query, a worker heartbeat, or the source's
metadata, in that order; unknown stays unknown rather than becoming an assumed 30).

For tracking workloads the plan targets **at least 15 processing FPS per camera**, preferring
30 or source-native where the source supplies it, because tracker association — and
therefore dwell, visits, flow and fusion — degrades with the gap between frames. Submission
Hz stays a separate, lower, task-chosen rate. A source slower than the floor gets its native
rate with `source_limited` set, rather than a recommendation it cannot meet.

`acceleration` and `local_environment` are decision procedures, not detections: ManySight
runs no models and may not even be on the worker's machine, so it describes the checks
(`nvidia-smi`, `torch.cuda` **inside the interpreter that will run the worker**, existing
virtualenv/conda environments) and the SDK's `probe_perception_runtime()` executes them
locally. CUDA is an optimization with a supported CPU fallback, never a connection
prerequisite.

The recipe asks the worker to report `source_fps`, `processing_fps`, `submission_hz`,
`device` and `precision` in heartbeat metrics, which is what lets `inspect_perception`
score achieved rate against target.

## `preview_zone` / `commit_zone` — approval before persistence

Zone geometry from camera evidence is subjective, so it is a two-phase operation:

```text
plan_frame_capture ──► look at the image ──► preview_zone ──► show the user
                                                  ▲                │
                                                  └── correction ◄──┘
                                                                   │
                                                            approval
                                                                   ▼
                                                             commit_zone
```

`preview_zone` projects each proposed pixel polygon through the source's floor homography
or a named projection surface and returns validity, area, calibration revision, the unioned
canonical preview, provenance, and calibration warnings — **persisting nothing**. It can be
called any number of times.

`commit_zone` requires `approved=true` and then runs the same validated low-level sequence
the platform has always used: create the canonical zone from the first contribution, create
one ZoneView per camera, and union the remaining contributions with explicit
`extend_zone_from_view` calls so each step records provenance. The result is **one**
canonical zone, never one per camera, and cameras that cannot see the region get no view.

## Workflows

`list_workflows()` is the routing index; `get_workflow(name)` returns prerequisites, an
ordered sequence, non-negotiable invariants, the tools that implement it, and what "done"
means. Current workflows:

`onboard-camera`, `inspect-source`, `define-zone-from-cameras`, `run-person-tracking`,
`configure-multiview`, `create-zone-occupancy-alert`, `create-generated-dashboard`.

`create-zone-occupancy-alert` also publishes the machine-readable threshold-phrase table
(`more than {n}` → `>`, `at least {n}` → `>=`, …) so an agent can map the user's own words
instead of picking whichever operator it saw last.

The registry lives in `server/services/agent_workflows.py` as pure data, so both the REST
surface and the tests read the same source of truth.

## Legacy and compatibility

Nothing was deleted. The 59 low-level handlers remain implemented as plain module
functions in `mcp_server/server.py` — importable, testable, and each documenting the
curated tool that supersedes it. They are simply not advertised.

A deployment that still drives the old tool names can re-advertise all of them:

```bash
MANYSIGHT_MCP_LEGACY_TOOLS=1 python mcp_server/server.py
```

That yields 78 tools (19 curated + 59 legacy) and is a migration path, not a
recommendation. REST and the SDK are unchanged, so no capability was removed from the
platform — only from the default agent surface.

`register_insight`, `list_insights`, and `delete_insight` remain retired compatibility
adapters and are never advertised in either mode.

## Evaluation

[`evals/agent_operability/`](../evals/agent_operability/README.md) holds scenarios derived
from real failures, including a transcription of the Codex session that motivated this
surface. `tests/test_agent_operability.py` checks that each scenario's golden path
satisfies its own rules, that the rule checker rejects the failures it exists to catch, and
that executing the golden path against the real platform produces the expected resources —
including an alert that fires at 3 and not at 2 when the user said "more than 2".

No language model runs in that suite. It proves the correct path is available and
enforced, not that a given model will choose it; `check_transcript.py` grades a real
recorded agent run against the same rules.
