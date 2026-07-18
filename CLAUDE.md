# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

StoreLens is the working POC infrastructure behind the **ManySight** physical-space
intelligence dashboard: logical observation sources, a floor plan with named zones, per-camera
pixel→meter homographies, a generic event stream, computed insights, and alerts. It
deliberately contains **zero hardcoded CV logic** — the analysis half is an external AI
coding agent (OpenAI Codex, or any MCP client) that opens cameras locally, writes and
runs worker scripts, and posts observations back over REST or MCP. The hosted platform
never opens feeds or stores camera connection URLs/credentials. `AGENTS.md` is that agent's
operating manual; `skills/*/SKILL.md` are its step-by-step playbooks.

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
  Set `STORELENS_MCP_TRANSPORT=streamable-http` for the separately hosted remote MCP service.
- There is no test suite or linter config in this repo currently.
- `requirements.txt` intentionally carries no version pins ("Latest stable versions; no
  pins.") — preserve that convention if you touch it.

## Architecture

**Two client surfaces, one server.** `server/` (FastAPI + plain SQLite, no ORM) is the
single source of truth. It's driven two ways: (1) the MCP/REST surface that analysis
workers and Codex use to read config and post events, and (2) the React dashboard, whose
static build the same FastAPI app serves at `/` — there is no separate frontend server in
production.

**The event ingestion pipeline is the core of the system** (`server/routers/events.py:ingest`).
Understanding it requires reading three files together:
1. A posted event may carry `bbox`, `point_px`, or `point_map`. If only `bbox` is given, the
   feet position (bottom-center of the box) becomes `point_px`.
2. If the event's `source_id` has a stored homography, `point_px` is projected to
   `point_map` (floor meters) via `services/homography.py` (normalized DLT, pure numpy).
3. If no explicit `zone_id`/`zone` name is given, the projected map point is tested against
   every zone polygon (`services/homography.py:point_in_polygon`) to auto-assign a zone.

After enrichment, events are persisted, `services/alert_engine.py` evaluates all enabled
alert rules synchronously against the just-inserted batch (webhooks fire on daemon threads
so ingestion never blocks on the network), and `services/sse.py`'s in-memory `broker`
fans the enriched events out over `/api/v1/stream` (capped at 25 events per batch, plus a
`batch_summary` marker for bulk backfills, so a large replay doesn't flood browsers).
Analytics endpoints (`server/routers/analytics.py`: summary/heatmap/dwell/occupancy/
transitions/states) are computed live from the `events` table on every read — there's no
precomputed/materialized layer.

**Derive-only contract**: workers post raw observations (detections, `zone_enter`/
`zone_exit` pairs, label-only `state_change` flips, per-frame `count` samples), never
computed aggregates. `services/derive.py` is the single home for dwell/state derivation
used by both analytics and the alert engine: dwell always comes from enter/exit pairs
(in-progress visits included, capped at `MAX_DWELL_S`), state durations from consecutive
`state_change` timestamps. `zone_dwell` events are deprecated — accepted and stored for
backward compat, but their values are never read. The alert engine also fires on ongoing
conditions (loiter without exit, state still active past threshold), evaluated per
ingested batch.

**Insight registry** (`server/routers/insights.py` + `insight_definitions` table): the
Insights tab renders only registered definitions — a title/question + a `block`
(metric/line/bar/table/heatmap_map/flow_matrix/state_timeline) + a `dataset` (an
analytics kind) + params. Block↔dataset compatibility is validated server-side
(`BLOCK_DATASETS`). `GET /insights/templates` assembles a data-aware template catalog
for the UI picker. Agents register insights over MCP (`register_insight`); pinned ones
also render on Overview. Never render arbitrary definition content — the frontend maps
blocks onto its fixed component registry (`dashboard/src/insights.jsx`).

**GET /events pagination**: keyset cursor on `(ts, id)` (`cursor` param, opaque
`"{ts!r}:{id}"`), response includes `total` and `next_cursor`. The Detections tab pages
with it; new inserts don't break open cursors.

**Zones are geometry + label, never behavior**: what a zone means (restricted, queue)
lives in alert rules and insights, not the zone row — workers posting enter/exit don't
know zone semantics. `POST /zones` also accepts `polygon_px` + `source_id` (projected
server-side through the camera homography, 409 if uncalibrated) so an agent can create
a zone from points selected on a locally captured frame; exposed over MCP as `create_zone`.

**DB layer** (`server/db.py`): raw `sqlite3` in WAL mode, dict rows, no migrations
framework — `init_db()` runs `CREATE TABLE IF NOT EXISTS` then hand-rolled `ALTER TABLE ...
ADD COLUMN` checks for columns added after the fact; follow that pattern when adding
columns. This is a **single-tenant POC**: most tables assume one store row (`id=1`,
hardcoded in `server/routers/store.py` and friends).

**MCP server** (`mcp_server/server.py`) is a thin bridge, not a direct import of `server/`
— every tool call is an HTTP request to the same REST API a human dashboard user would
hit. It also serves `list_skills()`/`get_skill(name)` by reading `skills/*/SKILL.md`
directly off disk, so Codex gets the same playbooks whether it's working inside this repo
or connected remotely. When you change event semantics or add an analytics endpoint, keep
the matching skill doc and this file's docstrings in sync.

**Worker SDK** (`sdk/python/storelens.py`): the client library that analysis workers
(scripts an external coding agent writes at runtime — not part of this repo's own runtime)
import. Provides the `StoreLens` REST client, a dependency-free `CentroidTracker` (greedy
nearest-centroid), and local (no-HTTP) `project`/`point_in_zone` helpers mirroring the
server's own homography/zone logic. `examples/*.py` are runnable reference workers
(shopper simulator, motion-based heatmap tracker, dwell, fridge state) — degrade gracefully
to background-subtraction blobs when OpenCV/ultralytics aren't available; crib from these
when changing the SDK contract.

**Dashboard** (`dashboard/`, React 19 + Vite, no react-router): `main.jsx` owns hash-based
routing (`#overview`/`#insights`/`#review`/`#detections`/`#sources`/`#configure`; legacy
`#events` and `#streams` redirect to their replacements) and the app shell (live SSE indicator, toasts, alert
badge). `api.js` is a thin fetch wrapper that reads the API key from `localStorage` and
appends it as `X-API-Key` or `?api_key=`. `pages.jsx` holds Overview/Review/Sources/
Configure; `insights.jsx` the registry-driven Insights catalogue (and `InsightCard`, also
used for Overview pinning); `detections.jsx` the raw-observation browser with its in-page
docs panel; `space-workbench.jsx`/`technical-config.jsx` the Configure sub-tabs. Shared
chart/table primitives (incl. `RangeSelect`, `FlowTable`, `StateSummary`, `DataTable`)
live in `components.jsx` — note `pages.jsx` imports from `insights.jsx`, so `insights.jsx`
must only import from `components.jsx`/`api.js` to avoid a cycle. `EventSource` on
`/api/v1/stream` drives live updates, reconciled with a periodic `refreshShell()` poll
every 5th SSE tick.

**Auth**: a single optional `STORELENS_API_KEY` env var, checked by a FastAPI middleware
for any `/api/*` path except `/api/v1/health` — no per-user accounts.
