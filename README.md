# StoreLens · ManySight dashboard POC

**The working camera-to-insight POC behind the ManySight physical-space intelligence dashboard.**

StoreLens provides the POC infrastructure for configurable video intelligence across
stores, schools, workplaces, warehouses, and other physical spaces: logical sources, a floor
plan with named zones, floor and named-plane pixel→meter homographies, per-camera decision
ROIs, a generic observation stream, analytics and alerts. It deliberately contains
**zero hardcoded CV logic**. The *analysis* half is an AI coding agent (OpenAI **Codex**,
or any MCP client): it looks at your cameras, picks models, writes worker scripts, runs
them, and submits observations back — guided by the **skills** shipped in this repo and
served over MCP.

**Observe locally, derive centrally.** A worker submits only three kinds of raw
observation — `detection`, `measurement`, `state` — and never resolves a zone or computes
dwell, occupancy, movement, or a state change; StoreLens derives all of that itself. See
[`docs/adr/0001-observation-contract.md`](docs/adr/0001-observation-contract.md) for why.

Camera access is deliberately worker-local. StoreLens never opens or proxies a feed.
Sources can use encrypted StoreLens-managed credentials or retain an external worker
secret reference. In both modes, workers open the webcam, RTSP/HTTP stream, or file on
the device/edge gateway where they run and send only observations over HTTPS.

```
 ┌──────────────┐   RTSP/HTTP/WebRTC/…   ┌─────────────────────────────┐
 │   cameras    │ ─────────────────────▶ │  workers (written by Codex)  │
 └──────────────┘                        │  detect · track · classify   │
        ▲  source metadata, map,         └──────────────┬──────────────┘
        │  map, zones, homography                       │ detection/measurement/state
 ┌──────┴───────────────── MCP + REST ────────────────▼──────────────┐
 │                        StoreLens server                            │
 │  Cameras · Space Map (walls/zones/calibration) · Observations      │
 │  Derived analytics (visits/dwell/occupancy/flow/states) · Alerts   │
 └──────────────────────────────┬────────────────────────────────────┘
                                │ SSE + charts
                        ┌───────▼────────┐          ┌────────────┐
                        │  Analytics UI   │          │ n8n / hooks │
                        └────────────────┘          └────────────┘
```

## Quickstart

```bash
pip install -r requirements.txt
npm install --prefix dashboard
npm run build --prefix dashboard
python scripts/seed_demo.py            # optional: fully populated demo store
uvicorn server.app:app --port 8000
```

Open **http://localhost:8000** — six operational sections:

1. **Dashboard** — live current-value cards (active entities, latest measurements, current states), tracked visits, activity map, pinned analyses, and recent reviewable signals.
2. **Analytics** — a user-curated catalogue of saved analyses (a subject + measures + filters + grouping — never a chart definition) rendered from the unified analytics query engine. Users add them from a capability-aware builder; agents save them over MCP.
3. **Review** — human review queue with new/in-review/resolved/dismissed states and notes.
4. **Observations** — every raw detection/measurement/state workers submitted: filterable, paginated, with in-page documentation of each column, kind, and the enrichment pipeline.
5. **Sources** — logical source provenance, observation freshness, event volume, and heartbeat-backed worker state.
6. **Setup** — workspace type, native floor-map editor, global polygon zones, source placement/FOV, stored geometry revisions, worker state, thresholds, API settings, and agent contract.

Live demo motion without any camera:

```bash
python examples/simulate_shoppers.py --shoppers 6 --minutes 10
```

## Connect Codex

Add the MCP server to `~/.codex/config.toml` (see `codex.config.example.toml`):

```toml
[mcp_servers.storelens]
command = "python"
args = ["/path/to/repo/mcp_server/server.py"]
env = { STORELENS_URL = "http://localhost:8000" }
```

For managed source credentials, configure two independent deployment secrets:

```text
STORELENS_CREDENTIAL_KEY=<URL-safe base64 encoding of exactly 32 random bytes>
STORELENS_CREDENTIAL_ACCESS_KEY=<strong key used only by authorized workers/MCP clients>
```

The first key encrypts credentials at rest with AES-256-GCM and is never generated or
stored by StoreLens. The second protects the explicit
`GET /api/v1/sources/{id}/connection` resolution endpoint. Normal source API responses,
the dashboard, and ordinary MCP discovery never return usernames or passwords. Set
`STORELENS_CREDENTIAL_ACCESS_KEY` in the MCP environment only when that MCP client is
authorized to launch local workers. See [`docs/source-connections.md`](docs/source-connections.md).

Then ask Codex things like:

> *"Measure dwell time of men vs women around the checkout and put it on my dashboard."*

Codex will: `get_skill("storelens-platform")` → `list_skills()` → load the closest recipe →
`list_sources()` / `create_source(...)` → inspect the camera locally → read
`get_store_map()` and its zone views/planes →
`register_job(...)` → write, register, and run a worker (see `examples/`) →
`submit_observations(...)` → `create_analysis(...)` → the card appears in **Analytics**.
The full agent contract is in [`AGENTS.md`](AGENTS.md); recipes are in
[`skills/`](skills/README.md).

## The observation contract (what makes it multi-purpose)

Workers submit raw observations — what the model saw, never computed aggregates — as
exactly one of three kinds. The platform preserves the model evidence and enriches each
row (bbox/keypoints/mask → representative point → selected-plane projection → zone-view
or map assignment) and **derives** every metric server-side, so numbers stay replayable
and explainable:

| you submit | the platform derives |
|---|---|
| `detection` + `geometry.point_px` + optional `label`/`entity_type` | floor heatmap, presence, and visit/dwell/flow — filterable/comparable by class |
| `measurement` + `name` + `value` (+ `value_kind`) | classifier population curve, queue length, any numeric trend — never sum a `gauge` sample yourself |
| `state` + `name` + `label`, sent on every sample including repeats | state timelines, coalesced intervals, derived durations, duration alerts |
| any observation + `attributes` | group-by splits in Analytics (e.g. dwell by gender) |
| `create_alert_rule` + `webhook_url` (legacy kinds, or the general `analysis_condition`) | toasts, alert log, n8n workflows — evaluated on a periodic timer, not only on ingestion |
| `create_analysis` (subject + measures + filters + grouping) | a live card in Analytics, optionally pinned to Dashboard |

Never send `zone_id`/`zone`, and never submit the retired derived kinds `zone_enter`/
`zone_exit`/`zone_dwell`/`state_change`/`count` — `POST /observations/batch` rejects them
per-item with `legacy_derived_observation`. The older `POST /events` contract with those
kinds still exists as a documented compatibility surface for historical integrations only.

## Geometry model

A physical zone and its appearance in a camera are deliberately separate:

- A **zone** is the canonical footprint in map metres. Editing it increments its
  revision; old events keep the revision used when they were ingested.
- A **zone view** belongs to one zone and one camera. It stores the visible outer
  polygon, an inset decision ROI, and a membership rule: representative point,
  bounding-box overlap, or pose-keypoints-inside.
- The source calibration is the **floor plane**. A **projection surface** is another
  horizontal plane for a mattress, table, shelf, conveyor, or platform, computed from
  at least four camera↔map point pairs. Its height is descriptive metadata. Never
  subtract physical height from map Y; a 2D homography has no vertical axis.

Agents configure calibration, zone views, and projection planes through MCP/REST using
frames captured on the worker device. The hosted dashboard never requests a camera frame.

Events retain `bbox`, `keypoints`, optional compressed `mask`, `point_kind`, the chosen
surface/view IDs, assignment method, and all geometry revisions. Detections therefore
remain explainable even after geometry is edited.

## Worker lifecycle

Jobs describe analyses; worker instances describe running processes. A worker registers
after launch and heartbeats every 5–15 seconds. The dashboard marks it stale after 30
seconds and can request stop or restart. The heartbeat response tells the worker to exit;
a deployment supervisor (systemd, Docker, Kubernetes, etc.) must perform the relaunch.
The StoreLens web process never executes arbitrary worker scripts.

## Repo layout

```
server/            FastAPI app: REST API, SSE, analytics, React build host
  routers/         sources · store · zones · geometry · jobs/workers · events (legacy) · observations ·
                    analytics (legacy per-kind) · analytics_query (unified) · analyses · alerts · insights (legacy)
  services/        plane homography (DLT) · polygon/box geometry · enrich (shared ingestion pipeline) ·
                    derive · SSE · alert engine (per-batch + periodic ongoing evaluator)
dashboard/         ManySight React/Vite operational dashboard
mcp_server/        MCP server for Codex (tools + skill discovery + analysis registry)
sdk/python/        storelens.py — worker SDK (client, tracker, projection)
skills/            agent playbooks: detection-tracking · measurement · state-observation ·
                    geometry-calibration · alerts-workflows · analytics
examples/          runnable workers: simulator · heatmap/dwell tracker · fridge state · measurement curve
scripts/           seed_demo.py — populated demo store + 3h of history + saved-analysis catalogue
docs/adr/          architecture decision records
tests/             pytest suite (written alongside the observation-contract redesign)
```

## Configuration

Public links and endpoint paths live in [`config/endpoints.json`](config/endpoints.json).
Choose a profile with `STORELENS_ENDPOINT_PROFILE`; deployment variables override its
URLs. The resolved registry is served at `/api/v1/platform-config`, so dashboards,
workers, discovery files, and MCP skills do not need a hard-coded deployment host.

| env | purpose |
|---|---|
| `STORELENS_API_KEY` | if set, `/api/*` requires `X-API-Key` (UI: ⚙ settings) |
| `STORELENS_DATA` | data dir (SQLite), default `./data` |
| `STORELENS_ENDPOINT_PROFILE` / `STORELENS_ENDPOINT_CONFIG` | selected endpoint profile and optional registry file |
| `STORELENS_PUBLIC_URL` / `STORELENS_PUBLIC_MCP_URL` | URLs advertised by `/agent.md` and discovery metadata |
| `STORELENS_CORS_ORIGINS` | comma-separated browser origins; defaults to local dashboard URLs |
| `STORELENS_URL` / `STORELENS_SKILLS` | upstream platform and skill path used by MCP |
| `STORELENS_MCP_TRANSPORT` | `stdio` or `streamable-http` |
| `STORELENS_MCP_HOST` / `STORELENS_MCP_PORT` | MCP bind address, default `127.0.0.1:8001` |

Agent discovery is served at `/agent.md`, `/llms.txt`, and
`/.well-known/storelens.json`. API reference: interactive OpenAPI docs at `/docs`.
