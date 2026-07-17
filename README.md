# StoreLens · ManySight dashboard POC

**The working camera-to-insight POC behind the ManySight physical-space intelligence dashboard.**

StoreLens provides the POC infrastructure for configurable video intelligence across
stores, schools, workplaces, warehouses, and other physical spaces: camera sources, a floor
plan with named zones, floor and named-plane pixel→meter homographies, per-camera decision
ROIs, a generic event stream, insights and
alerts. It deliberately contains **zero hardcoded CV logic**. The *analysis* half is an
AI coding agent (OpenAI **Codex**, or any MCP client): it looks at your cameras, picks
models, writes worker scripts, runs them, and posts events back — guided by the
**skills** shipped in this repo and served over MCP.

```
 ┌──────────────┐   RTSP/HTTP/WebRTC/…   ┌─────────────────────────────┐
 │   cameras    │ ─────────────────────▶ │  workers (written by Codex)  │
 └──────────────┘                        │  detect · track · classify   │
        ▲  sources, snapshots,           └──────────────┬──────────────┘
        │  map, zones, homography                       │ events (batched)
 ┌──────┴───────────────── MCP + REST ────────────────▼──────────────┐
 │                        StoreLens server                            │
 │  Cameras · Space Map (walls/zones/calibration) · Generic events    │
 │  Analytics (counts/heatmap/dwell/occupancy/flow) · Alerts+webhooks │
 └──────────────────────────────┬────────────────────────────────────┘
                                │ SSE + charts
                        ┌───────▼────────┐          ┌────────────┐
                        │   Insights UI   │          │ n8n / hooks │
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

1. **Overview** — tracked visits, occupancy, activity map, pinned insights, and recent reviewable signals.
2. **Insights** — a user-curated catalogue of registered insight definitions (metric, line, bar, table, floor heatmap, flow matrix, state timeline) rendered from derived platform analytics. Users add cards from data-aware templates; agents register them over MCP.
3. **Review** — human review queue with new/in-review/resolved/dismissed states and notes.
4. **Detections** — every raw event workers posted: filterable, paginated, with in-page documentation of each column, event type, and the enrichment pipeline.
5. **Streams** — camera snapshots, POC health, placement, calibration, and source management.
6. **Configure** — workspace type, native floor-map editor, global polygon zones, camera placement/FOV, guided floor calibration, camera-specific zone views and elevated projection planes, heartbeat-backed worker state, thresholds, API settings, and agent contract.

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

Then ask Codex things like:

> *"Measure dwell time of men vs women around the checkout and put it on my dashboard."*

Codex will: `get_skill("storelens-platform")` → `list_skills()` → load the closest recipe →
look at `get_snapshot(...)`, `get_store_map()` and its zone views/planes →
`register_job(...)` → write, register, and run a worker (see `examples/`) →
`submit_events(...)` → `register_insight(...)` → the card appears in **Insights**.
The full agent contract is in [`AGENTS.md`](AGENTS.md); recipes are in
[`skills/`](skills/README.md).

## The observation contract (what makes it multi-purpose)

Workers post raw observations — what the model saw, never computed aggregates. The
platform preserves the model evidence and enriches each row (bbox/keypoints/mask →
representative point → selected-plane projection → zone-view or map assignment)
and **derives** every metric server-side, so numbers stay replayable and explainable:

| you send | the platform derives |
|---|---|
| `detection` + `point_px` + optional `label` | floor heatmap and distinct-track presence, filterable/comparable by detected class |
| `count` + `label` + `value` (per-frame sample) | classifier population curve (children, vehicles, occupied desks, …) |
| `zone_enter`/`zone_exit` pairs | dwell stats (incl. in-progress visits), flow matrix |
| `state_change` + `label` (on flips) | state timelines, derived durations, duration alerts |
| any event + `attributes` | group-by splits (e.g. dwell by gender) |
| `create_alert_rule` + `webhook_url` | toasts, alert log, n8n workflows |
| `register_insight` (block + dataset + params) | a live card in Insights, optionally pinned to Overview |

(`zone_dwell` is deprecated: still accepted and stored, but its value is ignored —
dwell is always derived from enter/exit pairs.)

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

Configure this under **Configure → Space & zones → select camera → Zone views &
planes**. “Project map footprint into frame” generates the camera boundary from the
chosen plane and proposes a 10% inset decision ROI.

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
  routers/         sources · store · zones · geometry · jobs/workers · events · analytics · alerts · insights
  services/        plane homography (DLT) · polygon/box geometry · derive · snapshots · SSE · alert engine
dashboard/         ManySight React/Vite operational dashboard
mcp_server/        MCP server for Codex (tools + skill discovery + insight registry)
sdk/python/        storelens.py — worker SDK (client, tracker, projection)
skills/            agent playbooks: heatmap · dwell-time · state-monitoring · alerts-workflows · insights
examples/          runnable workers: simulator · heatmap tracker · dwell · fridge state
scripts/           seed_demo.py — populated demo store + 3h of history + insight catalogue
```

## Configuration

| env | purpose |
|---|---|
| `STORELENS_API_KEY` | if set, `/api/*` requires `X-API-Key` (UI: ⚙ settings) |
| `STORELENS_DATA` | data dir (SQLite + snapshots), default `./data` |
| `STORELENS_URL` / `STORELENS_SKILLS` | used by the MCP server |

API reference: interactive OpenAPI docs at `/docs` once the server runs.
