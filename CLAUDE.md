# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

StoreLens is the working POC infrastructure behind the **ManySight** physical-space
intelligence dashboard. It is built around one strict boundary: **workers submit only
direct observations — detection, measurement, state — and the platform derives zones,
visits, dwell, occupancy, movement, state transitions, aggregations, analytics, and
alerts.** It deliberately contains **zero hardcoded CV logic** — the analysis half is
an external AI coding agent (OpenAI Codex, or any MCP client) that opens cameras locally,
writes and runs worker scripts, and submits observations back over REST or MCP. The
hosted platform never opens feeds or stores camera connection URLs/credentials.
`AGENTS.md` is that agent's operating manual; `skills/*/SKILL.md` are its step-by-step
playbooks. `PLATFORM_ROADMAP.md` and `UI_UX_REWRITE_PLAN.md` are forward-looking/
historical planning docs, not descriptions of what's currently built (the latter's own
status line says the frontend migration it proposed is now implemented). `TUTORIAL.md` is
a manual walkthrough of the platform via curl/MCP, written for a specific local Windows
checkout — treat its paths as illustrative, not literal. `docs/adr/0001-observation-contract.md`
explains why this redesign shape was chosen.

## Commands

```bash
pip install -r requirements.txt          # fastapi, uvicorn, numpy, pillow, requests, mcp, opencv-python
npm install --prefix dashboard
npm run build --prefix dashboard          # required before the server will start — see below
python scripts/seed_demo.py               # optional: populated demo store + 3h of history
uvicorn server.app:app --port 8000        # or: ./run.sh (respects $PORT)
```

- `server/app.py` mounts `dashboard/dist` as the UI at `/` and **raises at startup** if that
  build doesn't exist — always `npm run build --prefix dashboard` after touching frontend code.
- Dashboard-only iteration: `npm run dev --prefix dashboard` (Vite dev server) / `npm run preview`.
- Live demo motion without a camera: `python examples/simulate_shoppers.py --shoppers 6 --minutes 10`.
- Standalone MCP server (for manual testing against a running StoreLens instance):
`python mcp_server/server.py`, configured via `STORELENS_URL`/`STORELENS_API_KEY`/`STORELENS_SKILLS`.
  Set `STORELENS_MCP_TRANSPORT=streamable-http` to serve it over HTTP instead of stdio.
- `python -m compileall server mcp_server sdk examples scripts` and `pytest` are the
  validation commands for backend changes (a minimal `pytest` suite lives under `tests/`,
  added alongside the observation-contract redesign — there was no test suite before).
  Install its extra deps first: `pip install -r requirements-test.txt` (pytest, httpx —
  kept separate from `requirements.txt` since they're dev-only). Run one file/test with
  `pytest tests/test_alerts.py -k test_name`. Each test gets an isolated SQLite db via the
  `isolated_db` fixture in `tests/conftest.py` (monkeypatches `server/db.py`'s
  `DATA_DIR`/`DB_PATH` globals) — never point tests at the real `./data` directory.
  `scripts/smoke_test.sh` is a curl+python smoke test of the platform slice — run it
  against a freshly seeded server (`uvicorn server.app:app --port 8000` +
  `python scripts/seed_demo.py`).
- `requirements.txt` intentionally carries no version pins ("Latest stable versions; no
  pins.") — preserve that convention if you touch it. `mcp` is the sole deliberate
  exception (`mcp>=2.0.0,<3`): the SDK's v1→v2 release renamed its server API in a way
  an unpinned install would silently break against (see the MCP server section below) —
  re-pin only after updating `mcp_server/_transport.py` for the next major SDK line.

## Architecture

**Two client surfaces, one server.** `server/` (FastAPI + plain SQLite, no ORM) is the
single source of truth. It's driven two ways: (1) the MCP/REST surface that analysis
workers and Codex use to read config and submit observations, and (2) the React dashboard,
whose static build the same FastAPI app serves at `/` — there is no separate frontend
server in production.

**The observation contract is the core of the system.** A worker submits exactly one of
three kinds — `detection` (an observed entity with spatial evidence), `measurement` (an
observed numeric value), `state` (an observed current categorical value) — via
`POST /api/v1/observations/batch` (`server/routers/observations.py`). A worker must never
send `zone_id`/`zone`, and never submit the retired derived kinds `zone_enter`/
`zone_exit`/`zone_dwell`/`state_change`/`count` — those are rejected per-item with a
`legacy_derived_observation` error. The legacy `POST /api/v1/events` endpoint
(`server/routers/events.py`) still exists, unchanged in behavior, as a documented
compatibility surface for old integrations and historical data — new code must use
`/observations`, never `/events`. **Both endpoints share exactly one enrichment
implementation**, `server/services/enrich.py::enrich_one` (plus `load_geometry_context`,
`update_counters`, `publish_batch`) — this is the single place that:
1. Picks the representative point in strict precedence: explicit `point_px`, then
   foot/ankle keypoints, then bbox bottom-center, then leaves it empty if only a mask is
   given. `/observations`' `bbox_px`/`point_px`/`keypoints_px` are normalized (corner-form
   bbox, `[x,y]` arrays, `{name:[x,y]}` dict) into the internal `bbox`/`point_px`/
   `keypoints` shapes before this runs.
2. **Zone view matching**: if no explicit `zone_id`/`zone`/`zone_view_id` is given and the
   source has registered zone views, each view is scored by its `membership_rule` (`point`
   inside the ROI polygon, `bbox_overlap`, or `keypoints_inside`) and the highest-scoring
   view above its `threshold` wins. `/observations` never accepts `zone_id`/`zone` at all.
3. **Projection**: an explicit `point_map` is trusted as-is (documented as the path for a
   trusted non-camera producer, e.g. a simulator — camera workers should send pixel
   evidence). Otherwise the pixel point is projected via `services/homography.py`
   (normalized DLT, pure numpy) using, in precedence order: an explicit
   `projection_surface_id`, the matched zone view's surface, or the source's floor calibration.
4. **Zone assignment**: the matched zone view's zone, otherwise the projected map point
   tested against every zone polygon (`homography.point_in_polygon`).
5. The stored row keeps the raw evidence plus which method was used and the zone/
   calibration/surface/zone-view **revision** in effect at ingestion — geometry edits
   affect only future rows.

After enrichment, observations are persisted (one `events` table serves both endpoints —
see "Schema" below), `services/alert_engine.py:evaluate_batch` evaluates completed
conditions for the just-inserted batch (webhooks fire on daemon threads so ingestion never
blocks on the network), and `services/enrich.py:publish_batch` fans them out over SSE.
**Ongoing/time-based alert conditions do not depend on ingestion at all**: `server/app.py`
runs a `lifespan`-managed asyncio task (`_alert_poll_loop`, interval
`STORELENS_ALERT_POLL_INTERVAL_S`, default 15s) that calls
`alert_engine.evaluate_ongoing` unconditionally — this is what makes loitering,
over-capacity, stuck-state, and unified `analysis_condition` alerts fire even when a zone
or series goes quiet, instead of only re-checking when another event happens to land there.

**Derivation** (`services/derive.py`) has two generations that are merged, not replaced:
- Legacy: `derive_dwells` pairs `zone_enter`/`zone_exit` rows; `state_before`/`current_state`
  read the latest `state_change` row.
- Current: `derive_visits_from_detections` groups ordered, zone-assigned `detection` rows
  by `(entity_id, zone_id)` into visit sessions — a gap ≤ `MAX_GAP_S` bridges missed
  frames, a visit only counts once it has ≥ `MIN_CONFIRM_SAMPLES` detections (so one noisy
  frame at a boundary is never a confirmed entry/exit), and the trailing session is
  "open" or "closed" depending on whether the last sample is within `MAX_GAP_S` of `now`.
  `coalesce_state_intervals` merges consecutive identical `state` samples into intervals
  (repeated identical samples must never inflate the transition count) and marks the
  trailing interval `stale` once its last sample is older than `STATE_STALE_S`, rather than
  extending it forever. `aggregate_measurement` respects `value_kind` (`gauge`: never
  summed; `delta`: summed and rated; `cumulative`: counter-reset-aware rate, never negative).
- `derive_visits(since, until, zone_id)` = legacy + current, concatenated — this is what
  `analytics.py:dwell()` and the alert engine call, so historical and current-contract data
  both contribute to the same numbers. `analytics.py:transitions()`/`states()` likewise
  read `event_type IN ('zone_enter','detection')` / `('state_change','state')` together.

**Latest-value read models** (`server/routers/observations.py:latest_observations`,
`GET /observations/latest`): current detections (active entities + last-seen + staleness),
current measurements (latest sample per source+name+label+entity), current states
(current label + duration + staleness per source+name+entity) — all computed live from
the same `events` table at query time (window-function "latest row per key" pattern,
matching how `analytics.py` has always worked), never a separate materialized copy.

**Unified analytics query engine** (`server/routers/analytics_query.py`):
`POST /analytics/query` answers one `{subject, measures, filters, grouping, range,
comparison}` question for any of the three subjects and returns a typed
`{shape, dimensions, measures, rows, metadata}` — `shape` (`scalar`/`timeseries`/
`categorical`/`heatmap`) tells the frontend how to render, never which chart to draw.
`GET /analytics/capabilities` exposes valid measures per subject plus the labels/sources/
zones/measurement-names/state-names/attribute-keys actually present, so the frontend and
MCP never have to duplicate server compatibility rules. The legacy per-kind endpoints
(`server/routers/analytics.py`: summary/heatmap/dwell/occupancy/counts/transitions/states)
remain, unchanged, as the read path for `/events`-shaped questions and for backward
compatibility — `analytics_query.py` is a second, complementary read surface over the same
table, not a rewrite of the first.

**Unified saved analyses** (`server/routers/analyses.py` + `analyses` table) replace the
block+dataset+params insight model: a saved analysis is `{subject, measures, filters,
grouping, presentation}` — `presentation` is a cosmetic renderer hint, changing it never
creates a second record (`db.py:analysis_hash` normalizes the analytical identity for
duplicate detection). The legacy `insight_definitions` table and `server/routers/insights.py`
remain for historical rows; `db.py:_migrate_insights_to_analyses` runs once at `init_db()`
and best-effort-translates every existing insight into an `analyses` row
(`migrated_from_insight_id` + `migration_note` explain the mapping — nothing is silently
dropped, even when the mapping is approximate).

**Geometry model — zone, zone view, projection surface are three different things**
(`server/routers/geometry.py`, `server/routers/zones.py`) — unchanged by the observation
contract redesign:
- A **zone** is the canonical footprint in map metres. Editing it increments its `revision`.
- A **zone view** belongs to one zone *and* one camera. Stores the visible outer polygon,
  an optional inset detection ROI, and a membership rule (`point`/`bbox_overlap`/
  `keypoints_inside`). `POST /zones` also accepts `polygon_px` + `source_id` directly.
- A **projection surface** is an additional named pixel→map homography for a plane other
  than the floor, computed the same DLT way from ≥4 point pairs. `height_m` is descriptive
  metadata only — never subtracted from map Y.
- Zones remain geometry + label, never behavior — workers never resolve or send one.

**GET /observations pagination** (and legacy `/events`): keyset cursor on `(ts, id)`
(`cursor` param, opaque `"{ts!r}:{id}"`), response includes `total` and `next_cursor`. The
Observations tab pages with it; new inserts don't break open cursors.

**Schema** (`server/db.py`): raw `sqlite3` in WAL mode, dict rows, no migrations framework —
`init_db()` runs `CREATE TABLE IF NOT EXISTS` then hand-rolled `ALTER TABLE ... ADD COLUMN`
checks; follow that pattern when adding columns. The observation contract redesign is
**additive**: the `events` table gained `schema_version`, `observation_id` (unique
idempotency key, partial index `WHERE observation_id IS NOT NULL`), `worker_id`, `name`,
`entity_type`, `value_kind`, `unit`, `confidence`, `identity_scope`,
`identity_model_version` — legacy rows keep `schema_version=1` and their original
`event_type`; new rows use `schema_version=2` and `event_type` holds the new `kind`.
`track_id` doubles as the API's `entity_id`; `label`/`attributes` carry per-kind meaning
(documented in `docs/adr/0001-observation-contract.md`). `alert_rules` gained
`analysis_json`/`condition_json`/`condition_state_json` for the unified `analysis_condition`
kind, additive alongside the legacy `kind`/`params_json`. This is a **single-tenant POC**:
most tables assume one store row (`id=1`, hardcoded in `server/routers/store.py` and friends).

**MCP server** (`mcp_server/server.py`) is a thin bridge, not a direct import of `server/`
— every tool call is an HTTP request to the same REST API a human dashboard user would
hit. Built on MCP Python SDK v2 (`mcp>=2.0.0,<3` — the one pinned exception to
`requirements.txt`'s no-pins convention, see below); `mcp_server/_transport.py` is the
sole compatibility boundary that imports `MCPServer`/`TransportSecuritySettings`/
`ToolError` and constructs/runs the server — `server.py` itself only calls
`build_server(...)`/`run_server(...)` and never imports SDK server/transport classes
directly, so a future SDK migration touches only `_transport.py`. The `@mcp.tool()`
decorator surface is unchanged from v1; `host`/`port`/`stateless_http`/
`transport_security` moved off the server constructor onto `run()` in v2 — `run_server`
only forwards them for the `streamable-http` transport, never `stdio`. Primary tools:
`submit_observations`, `get_observation_contract`, `list_observations`,
`get_latest_observations`, `query_analytics`, `list_analysis_capabilities`,
`create_analysis`/`list_analyses`/`update_analysis`/`delete_analysis`. Legacy tools
(`submit_events`, `get_analytics`, `register_insight` — now a best-effort adapter onto
`create_analysis` — `list_insights`/`delete_insight`, read/delete only: the REST
`insights` router dropped create/update entirely, since authoring a new insight in the
old shape is exactly what the redesign removed) remain, docstring-
flagged as legacy, for historical/backward-compat use. Also exposes the full geometry
surface (`create_zone`, `create_projection_surface`/`create_zone_view` + their `list_`/
`update_`/`delete_` counterparts, `project_points`/`unproject_points`) and worker lifecycle
tools (`register_job`, `register_worker`, `heartbeat_worker`, `request_worker_state`), and
serves `list_skills()`/`get_skill(name)` by reading `skills/*/SKILL.md` off disk. When you
change observation semantics or add an analytics endpoint, keep the matching skill doc and
this file's docstrings in sync.

**Worker SDK** (`sdk/python/storelens.py`): the client library that analysis workers
(scripts an external coding agent writes at runtime — not part of this repo's own runtime)
import. Primary methods: `submit_detection`/`submit_measurement`/`submit_state` (buffered,
auto-flush at `batch_size`, idempotency-keyed) and `query_analytics`/`save_analysis`.
`add_event`/`post_events`/`flush` (the legacy `/events` contract) still work — `add_event`
emits a `DeprecationWarning` when `event_type` is one of the retired derived kinds. Also
provides a dependency-free `CentroidTracker` (greedy nearest-centroid) and local (no-HTTP)
`project`/`point_in_zone` helpers. `examples/*.py` are runnable reference workers (shopper
simulator, motion-based heatmap/dwell tracker, fridge state, a synthetic measurement curve)
— every one now submits only detection/measurement/state observations; crib from these
when changing the SDK contract.

**Endpoint config & discovery** (`server/platform_config.py` + `config/endpoints.json`):
a small JSON registry of named profiles (currently just `local`), each with a `public_url`,
`mcp_url`, and `cors_origins`. `STORELENS_ENDPOINT_PROFILE` picks the active profile;
`STORELENS_PUBLIC_URL`/`STORELENS_PUBLIC_MCP_URL`/`STORELENS_CORS_ORIGINS` override it
without editing the file — useful if you self-host behind a reverse proxy or a non-default
port. The resolved registry drives CORS setup in `server/app.py` and is served at
`/api/v1/platform-config`, plus agent-discovery endpoints `/agent.md` (alias `/agend.md`),
`/llms.txt`, and `/.well-known/storelens.json` — all rendered from the same resolved URLs
so a remote agent can self-onboard, and all updated to describe the observation contract.
`STORELENS_PUBLIC_READS=true` lets unauthenticated `GET`s through the `STORELENS_API_KEY`
guard. This project ships no hosted deployment of its own — it's run locally by whoever
clones it; there is no separate container/cloud path.

**Dashboard** (`dashboard/`, React 19 + Vite, no react-router): `main.jsx` owns hash-based
routing. Current routes: `#overview` (Dashboard — pinned analyses + live current-value
cards), `#analytics` (`analytics.jsx` — the unified analysis builder/list, replacing the
retired block+dataset insight model), `#review`, `#observations` (`observations.jsx` — the
raw-observation browser with its in-page docs panel, replacing `detections.jsx`),
`#sources`, `#setup` (`pages.jsx:ConfigurePage` — unchanged internally, just relabeled).
Legacy hash bookmarks `#events`/`#streams`/`#insights`/`#detections`/`#configure` redirect
to their current names. `api.js` is a thin fetch wrapper (now with `patch`, used by
`PATCH /analyses/{id}`) that reads the API key from `localStorage` and appends it as
`X-API-Key` or `?api_key=`. `analytics.jsx` exports `AnalysisCard` (used both on its own
page and pinned on Dashboard) and must only import from `components.jsx`/`api.js` to avoid
an import cycle with `pages.jsx`. Shared chart/table primitives (`RangeSelect`, `FlowTable`,
`StateSummary`, `DataTable`, `MultiLineChart`, `ActivityMap`, ...) live in `components.jsx`.
`EventSource` on `/api/v1/stream` drives live updates — the stream now also carries
normalized event names (`observation.created`, `current_detection.updated`/
`current_measurement.updated`/`current_state.updated`, `analysis.invalidated`,
`alert.created`, `worker.updated`) alongside the legacy `cv_event`/`batch_summary`/`alert`
so old and current dashboard builds both work against one stream — reconciled with a
periodic `refreshShell()` poll every 5th SSE tick.

**Auth**: a single optional `STORELENS_API_KEY` env var, checked by a FastAPI middleware
for any `/api/*` path except `/api/v1/health` — no per-user accounts. `CORSMiddleware` is
registered *after* this middleware in `server/app.py` so it ends up outermost (Starlette
runs the last-registered middleware first) — otherwise a cross-origin preflight gets
401'd by the guard before CORS headers are ever attached. When
`STORELENS_PUBLIC_READS=true`, unauthenticated `GET`/`HEAD`/`OPTIONS` requests bypass the
key check (writes still require it) — used for public demo deployments.
