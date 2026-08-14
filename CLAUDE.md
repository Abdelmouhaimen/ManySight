# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Required reading order

1. [`AGENTS.md`](AGENTS.md) — canonical agent-facing operating manual (observation contract,
   source access, geometry, worker lifecycle, analytics, alerts).
2. [`skills/storelens-core/SKILL.md`](skills/storelens-core/SKILL.md) — load first for
   every StoreLens task, then the closest playbook from [`skills/`](skills/README.md).
3. [`docs/agent-surface.md`](docs/agent-surface.md) — the three-interface split (REST/SDK vs
   curated MCP vs skills), the exact 18-tool public surface, and the legacy strategy.
4. `GET /api/v1/observations/contract`, `GET /api/v1/agent/worker-recipe`, and
   `/openapi.json` are the authoritative runtime contracts; the docs describe them but never
   override them. An example or demo script on disk is never the contract.

Human-facing documentation starts in [`README.md`](README.md). Do not copy agent
instructions into public user documentation or expose source credentials.

## Non-negotiable invariants

- **Observe locally, derive centrally.** Workers submit only `detection`, `measurement`, and
  `state`. They must never submit `zone_id`/`zone`, `zone_enter`, `zone_exit`, `zone_dwell`,
  `state_change`, `count`, or any calculated analytic. The current ingestion path rejects
  those kinds (`enrich.LEGACY_DERIVED_KINDS`); legacy `/api/v1/events` still accepts them for
  historical compatibility only. Do not reintroduce them as worker output.
- **One complete sample per processed frame.** A person-detection worker submits one atomic
  `DetectionSample` for every processed frame, including empty ones (`detections=[]`, a real
  observed zero). All rows of a frame share one exact timestamp and one opaque `sample_id`;
  prefer the SDK sample builder. Never fake a detection to represent an empty frame. Legacy
  detection rows completed by a matching `detection_frame_count` measurement stay readable but
  are not the contract for new workers.
- **`entity_id` is an opaque tracker ID, not an identity.** Cross-camera association exists
  only inside explicit calibrated multiview groups and stays anonymous active-track
  association — never verified identity, never appearance/biometric ReID.
- **Credentials never travel with data.** Resolve them only in an authorized local worker, in
  memory; keep them out of observations, fused state, queries, dashboards, logs, code, and job
  metadata. StoreLens does not proxy feeds or execute worker scripts.
- **Space and observation reinitialization are destructive exact-confirmation operations.**
  Never invoke them without an explicit user request. Retained observations belong to their
  recorded `space_revision_id`; deleted-zone references stay unresolved and must not be
  re-matched by name.
- The guided demo is the one narrow exception for bundled sample media: allowlisted local
  NVIDIA assets plus a versioned fixture replayed with `producer_kind=replay`, no worker
  heartbeat. Never describe replay as live inference or live fusion.

## Commands

Docs use PowerShell; the bash equivalents are below. Python 3.11+ and Node.js 20+.

```bash
python -m venv .venv && source .venv/bin/activate
python -m pip install -r requirements.txt -r requirements-test.txt
npm install --prefix dashboard
npm run build --prefix dashboard          # REQUIRED before importing server.app
python -m uvicorn server.app:app --host 127.0.0.1 --port 8000   # or bash run.sh (0.0.0.0, $PORT)
npm run dev --prefix dashboard            # Vite dev server, run alongside the API
```

`server/app.py` mounts `dashboard/dist` at import time and raises if it is missing, so the
dashboard build is a hard prerequisite for the server **and** for the pytest suite.

```bash
python -m pytest -q                                   # full Python suite
python -m pytest -q tests/test_multiview_fusion.py    # one file
python -m pytest -q tests/test_alerts.py -k cooldown   # one test
npm test --prefix dashboard                           # node --test over dashboard/tests/*.test.mjs
node --test dashboard/tests/live-state.test.mjs       # one dashboard test file
bash scripts/smoke_test.sh                            # API smoke check against a running server
python -m pytest -q tests/test_agent_surface.py tests/test_agent_operability.py
python evals/agent_operability/check_transcript.py <scenario> <recorded-run.json>
```

No formatter, linter, or type checker is configured. Match surrounding Python/React style.

MCP server (stdio by default; `STORELENS_MCP_TRANSPORT=streamable-http` for HTTP):

```bash
STORELENS_URL=http://127.0.0.1:8000 python mcp_server/server.py
```

Demo tooling — `scripts/seed_demo.py` is **destructive** to the selected `STORELENS_DATA`
database. The fixture builder uses a temporary workspace instead, and rewrites the committed
cache under `demo/fixtures/`:

```bash
python demo/fetch_nvidia_mv3dt.py                                   # optional NVIDIA media
python demo/validate_mv3dt_fixture.py demo/fixtures/nvidia_mv3dt_yolo11n_bytetrack.jsonl
python demo/build_mv3dt_demo_fixture.py                             # rebuild derived replay cache
python scripts/seed_demo.py                                         # replaces workspace data
```

Environment variables are tabulated in [`docs/development.md`](docs/development.md).

## Architecture

Four surfaces sit on one REST contract: the FastAPI platform (`server/`), the ManySight React
dashboard (`dashboard/`), the MCP adapter (`mcp_server/`), and the worker SDK
(`sdk/python/storelens.py`, imported via `sys.path`, not installed as a package). The MCP
server is a thin REST client — it holds no business logic and never processes video.

### The curated agent surface

`mcp_server/server.py` advertises **18** semantic tools and keeps the 59 superseded
low-level handlers as plain undecorated module functions (`LEGACY_TOOLS`), re-advertised
only with `STORELENS_MCP_LEGACY_TOOLS=1`. Because MCP must stay a thin client, every
semantic operation is a real endpoint in `server/routers/agent_ops.py` under
`/api/v1/agent/*`: workspace snapshot, source detail, frame-capture plan, perception
capability, worker recipe, zone preview/commit, and the workflow index from
`server/services/agent_workflows.py`. That router adds no derivation — it reads the same
materialized models and calls the same routers the dashboard uses, and it never returns
connection material. Perception capability is *derived* from existing source/job/worker/
observation rows; there is no capability table and this surface needed no migration.
Details and rationale in [`docs/agent-surface.md`](docs/agent-surface.md).

### The single ingestion pipeline

Everything funnels through `server/routers/observations.py::_process_observations`, run off the
event loop with `asyncio.to_thread`:

1. validate (idempotent `observation_id` dedupe, `sample_id` timestamp/marker consistency,
   rejected kinds) →
2. `services/enrich.py::enrich_one` — the **only** geometry implementation: representative
   point → projection surface → homography → zone-view match → canonical zone → geometry
   revision bookkeeping. Legacy `/events` and `/observations/batch` both call it →
3. append-only insert into `events` →
4. `services/current_state.py::materialize_affected` — completion-gated read model; a sample
   commits only when its detection count matches its marker. Partial samples stay as raw
   evidence but never replace current scene state →
5. `services/multiview.py::process_completed_samples` — deliberately downstream of complete
   samples, imported lazily →
6. `services/alert_engine.py::evaluate_batch` →
7. `services/enrich.py::publish_batch` → SSE broker (`services/sse.py`), which fans out both
   legacy and current event names and is partitioned by database path.

`POST /api/v1/detection-samples` is the preferred worker entry point; it expands one envelope
into that batch and content-hashes the sample so a replay is a `duplicate`, while a conflicting
reuse of a `sample_id` is a 409.

Scene contents and freshness are independent: a stopped worker keeps its last complete sample
and goes stale — elapsed wall time never fabricates an empty scene.

### Derivation, queries, alerts

- `services/derive.py` derives dwell visits and state durations; worker-sent aggregates are
  stored but ignored. Analytics and the alert engine share it so the logic exists once.
- `routers/analytics_query.py` is one deterministic engine over
  `(subject, measures, filters, grouping, range, comparison)` for all subjects incl.
  `fused_entity`. Agents never get SQL. Presentation is not part of a query's identity —
  `dashboard/src/analytics.jsx` picks a renderer from the response `shape`, so a presentation
  change must not duplicate a saved query.
- Alerts fire from two places: per-batch during ingestion, and the periodic
  `_alert_poll_loop` in `server/app.py`'s lifespan (`STORELENS_ALERT_POLL_INTERVAL_S`, default
  15s) for ongoing conditions. That loop also refreshes multiview freshness and expires demo
  sessions, and must never die on a transient error.

### Storage

`server/db.py` is plain `sqlite3` (WAL, dict rows) with helpers `q`/`q1`/`ex`/`exmany`/`jload`/
`now`. There is no ORM and no migration framework: extend the `SCHEMA` string and add an
additive `PRAGMA table_info`-guarded `ALTER TABLE` inside `init_db()`. Raw observations are
append-only; current/fused state are bounded read models rebuilt via
`current_state.rebuild_from_history()` at startup. Every observation records
`space_revision_id`, and queries default to `db.current_space_revision_id()` so archived
geometry cannot contaminate current state.

### Request middleware and workspace isolation

In `server/app.py`, order matters (Starlette runs the last-registered middleware first): CORS
must stay outermost so key-protected deployments answer preflights. `api_key_guard` enforces
the optional `STORELENS_API_KEY`, exempting `/health` and the header-only source-connection
endpoint, which has its own stronger `STORELENS_CREDENTIAL_ACCESS_KEY` check.
`demo_workspace_guard` routes any request carrying `X-StoreLens-Demo-Session` into an isolated
temporary SQLite database through `db.using_database()` (a `ContextVar`), so demo sessions never
touch the real workspace. Temporary paths are never returned publicly; promotion copies a strict
setup allowlist in one transaction.

### Guided demo

Three separate stages, documented in [`docs/guided-demo.md`](docs/guided-demo.md): offline
fixture generation (NVIDIA video → YOLO11n + ByteTrack → raw `DetectionSample` JSONL), offline
cache generation (that fixture through the *real* StoreLens pipeline), and playable runtime
(`server/services/demo_runtime.py` + `dashboard/src/demo-replay*.js`) which advances one master
clock over the committed provenance-hashed cache. Runtime needs no GPU or weights and performs
no inference, projection, fusion, query recomputation, or alert evaluation.

## Testing conventions

`tests/conftest.py` gives every test a fresh SQLite file by monkeypatching `db.DATA_DIR`/
`db.DB_PATH` before `init_db()`. The `app` fixture reloads `server.app` because that module runs
`init_db()`, `rebuild_from_history()`, and the static mount at import time and reads
`STORELENS_API_KEY` at import time — tests needing auth set the env var before importing (see
`tests/test_api_auth.py`). `calibrated_source` provides a 1:1 100px = 1m mapping so projected
coordinates are hand-checkable.

Agent-operability scenarios live in `evals/agent_operability/` as JSON specs plus a pure,
dependency-free rule checker (`rules.py`). `tests/test_agent_operability.py` checks each
scenario's golden path against its own rules, checks that deliberately broken transcripts
are rejected, and executes the golden path against the real app. No LLM runs in CI;
`evals/agent_operability/check_transcript.py` grades a recorded real agent run.

## Dependency notes

`requirements.txt` is deliberately unpinned except `mcp>=2.0.0,<3`. All MCP SDK version
knowledge lives in `mcp_server/_transport.py` (`build_server`/`run_server`); when the SDK ships
v3, update that file and its tests, then re-pin — do not import SDK server classes elsewhere.

## Repository hygiene

`data/`, `dashboard/dist/`, and `dashboard/node_modules/` are gitignored. Keep source URLs,
credentials, recordings, and database files out of commits. Use concise conventional commit
messages (`fix(api): ...`, `docs: ...`); see [`CONTRIBUTING.md`](CONTRIBUTING.md).
