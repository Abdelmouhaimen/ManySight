# ManySight

ManySight is an open-source platform for turning observations from cameras and
sensors into spatial and temporal analytics for physical spaces. It stores source
configuration and mapped geometry, accepts raw observations from local workers,
projects spatial evidence into a floor plan, associates source-local tracks across
explicit calibrated camera groups, and derives visits, dwell, occupancy,
transitions, saved queries, dashboards, and alerts.

Released under the [Apache License 2.0](LICENSE).

> **Scope:** ManySight stores and derives; it does not run models. It uses SQLite,
> represents mapped surfaces with planar homographies, and relies on external
> worker processes for inference. Deployments are responsible for TLS, network
> controls, key rotation, and backups.

## What ManySight does

- Registers camera, video, file, and sensor sources with managed or external-secret
  connection configuration.
- Stores a metric floor plan, zones, camera placement, floor calibrations,
  projection surfaces, and per-camera zone views.
- Accepts schema-v2 `detection`, `measurement`, and `state` observations.
- Materializes complete processed samples and maintains source-local current state.
- Associates anonymous tracks across explicitly configured, shared-world camera groups.
- Assigns zones and derives visits, dwell, occupancy, transitions, state intervals,
  saved-query results, generated dashboards, and alerts from observations.
- Exposes a web dashboard, REST/OpenAPI API, Python worker SDK, and MCP server.

ManySight does not prescribe a computer-vision model or camera vendor, treat
anonymous tracker IDs or fused tracks as verified identities, proxy camera feeds
through the platform server, run appearance/ReID models centrally, or execute
arbitrary worker code inside the server process.

## Architecture

The core rule is **observe locally, derive centrally**:

```text
Camera / video / sensor
        |
        v
local worker (capture, inference, tracking)
        |
        v
raw detection / measurement / state observations
        |
        v
ManySight
  geometry enrichment -> zone assignment -> temporal derivation
        |
        v
source state / multiview fusion / queries / alerts / ManySight dashboard
```

Workers open sources where the device or stream is reachable. They submit direct
evidence, not ManySight-owned results: no canonical zone IDs, entry/exit events,
dwell, occupancy, transitions, state changes, or dashboard aggregates. ManySight
records the geometry revisions used at ingestion and derives higher-level results.

Read [Architecture](docs/architecture.md) and the
[observation-contract decision](docs/adr/0001-observation-contract.md) for details.

## Quick start

Prerequisites are Python 3.11+ and Node.js 20+.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt -r requirements-test.txt
npm install --prefix dashboard
npm run build --prefix dashboard
python -m uvicorn server.app:app --host 127.0.0.1 --port 8000
```

On macOS or Linux, activate the environment with `source .venv/bin/activate`.
The built dashboard is served by FastAPI, so build it before importing
`server.app` or starting the server.

Once running:

| Interface | URL |
|---|---|
| ManySight dashboard | <http://127.0.0.1:8000/> |
| Interactive API | <http://127.0.0.1:8000/docs> |
| OpenAPI schema | <http://127.0.0.1:8000/openapi.json> |
| Health | <http://127.0.0.1:8000/api/v1/health> |
| Runtime endpoint configuration | <http://127.0.0.1:8000/api/v1/platform-config> |
| Agent guide | <http://127.0.0.1:8000/agent.md> |
| Agent workspace snapshot | <http://127.0.0.1:8000/api/v1/agent/workspace> |

The default SQLite database is `data/manysight.db`.

### Finding your way around ManySight

The dashboard has six areas, each answering one question:

| Area | Answers | Contains |
|---|---|---|
| **Dashboard** | What am I monitoring? | Saved result views, rendered server-side |
| **Live** | What is happening now? | The floor map, current tracks, Combined / Per camera |
| **Review** | What needs attention? | **Alerts** (fired occurrences) and **Rules** (the conditions) |
| **Observations** | What raw evidence exists? | Filterable raw rows with expandable payloads |
| **Sources** | Where does data come from, and is it working? | Camera list, per-source detail, add/edit |
| **Setup** | How is the physical space configured? | **Space** (floor plan, zones), **Cameras** (placement, calibration, combined tracking), **Advanced** |

Add cameras in **Sources**, then trace a floor plan and draw zones in
**Setup → Space**, then place and calibrate each camera in **Setup → Cameras**.
Workspace name, workers, API access and the destructive reinitialize operations
live in **Setup → Advanced**. **Try Demo** in the top bar is the only entry to
the guided demo.

The interface uses product vocabulary — *alert rule*, *alert*, *combined
tracking*, *quality* — while the platform keeps its precise internal terms.
*Combined tracking* is the multiview fusion group; *quality* is the
known/partial/unknown guarantee; a result whose quality is unknown renders as
`—`, never as a confident zero. Internal identifiers, raw payloads and
calibration metadata sit behind **Technical details** and **Advanced**
disclosures. Agents and the API keep the precise names — see
[the agent operating surface](docs/agent-surface.md).

## Try the guided four-camera demo

The dashboard's **Try Demo** entry uses cameras 1-4 of NVIDIA's synthetic `mtmc_12cam`
warehouse sample. A versioned raw YOLO11n + ByteTrack `DetectionSample` fixture is
derived offline through the real ManySight geometry, multiview, saved-query, and alert
pipeline. Playable runtime uses one lightweight master clock to present the four native
videos, exact frame boxes, interpolated fused positions, stepwise KPI, and alert events
from that committed derived cache. Runtime needs neither a GPU nor model weights and
runs in an isolated temporary workspace.

The exact four camera recordings and bird's-eye plan required at runtime are committed
under `demo/assets/guided_demo/`. A normal clone only needs the standard install and
server command above; then select **Try Demo**. It does not download the full NVIDIA
dataset, require a GPU, or need an asset-path environment variable.

The fixed walkthrough alerts when at least two anonymous fused tracks are in either of
the camera-authored Aisle 04 footprint.
It supports discard or explicit setup-only promotion. See the
[guided-demo architecture, provenance, and asset terms](docs/guided-demo.md).

A guided progress card, slight dimming, and a spotlight explain each step on top of the
real interface. You can let ManySight present the prepared space and calibrations, or
choose **Show me how** and work through the actual plan digitizer and Camera 1
calibration controls; both paths continue from the same validated demo state.

## Source connections

A source combines a logical device with non-secret connection configuration.
Sensitive authentication material is stored or resolved separately:

- `manysight_managed` stores validated connection fields on the source and encrypts
  credentials in the database. An authorized worker resolves the connection through
  a dedicated, header-authenticated endpoint.
- `external_secret` stores only `locator.local_secret_ref`; the worker resolves that
  reference from its own environment, keychain, or ignored configuration.

Normal source discovery never returns credentials. ManySight still does not connect
to the feed: source resolution only gives an authorized local worker the information
it needs to open the source itself. See
[Source connections and credentials](docs/source-connections.md) for deployment keys,
supported fields, backup implications, and trust boundaries.

## Minimal worker

After creating a source in the dashboard or API:

```python
import time
import sys

sys.path.insert(0, "sdk/python")
from manysight import ManySight

client = ManySight("http://127.0.0.1:8000", api_key="")
source = client.source(1)
capture = client.open_capture(source)

client.register_job(
    "Person detections",
    "Tracked person observations from source 1",
    source_ids=[source["id"]],
    event_types=["detection"],
)
client.register_worker("person-detector", version="1")

ok, frame = capture.read()
if ok:
    client.submit_detection_sample(
        source_id=source["id"],
        entity_type="person",
        sample_id="camera-1-frame-42",
        timestamp=time.time(),
        frame_index=42,
        detections=[{
            "entity_id": "track-1",
            "point_px": [320, 470],
            "identity_scope": "source",
        }],
    )
```

Run a real worker as a long-lived process, heartbeat every 5–15 seconds, and obey
cooperative stop/restart requests. The SDK lives at
[`sdk/python/manysight.py`](sdk/python/manysight.py); examples are in
[`examples/`](examples/). See [Workers and observations](docs/workers.md) before
writing a new integration.

The Live view represents the latest complete processed sample for each source. Submit
`detections=[]` to record a successfully processed zero-detection frame. If processing
stops, the last scene remains visible with stale-source status; ManySight does not
reinterpret missing observations as an empty scene. Legacy detection rows completed by
a `detection_frame_count` measurement remain readable for backward compatibility.

For overlapping cameras, import compatible world calibrations and create a
multiview group. Live defaults to anonymous fused tracks and retains a source-local
debug mode. Fusion is geometry-first and deterministic; it does not create a
biometric identity or claim continuity outside its configured active-track lifetime.

## MCP

The MCP server is a curated agent-facing adapter over the REST API. It holds no business
logic and does not process video.

Three interfaces, three jobs:

| interface | role |
|---|---|
| REST + SDK | the complete low-level platform interface; `/openapi.json` is authoritative |
| MCP | a small semantic surface for coding agents — 19 tools, not one per endpoint |
| [`skills/`](skills/README.md) | the workflow knowledge behind those tools |

An agent starts with `inspect_workspace()` for one snapshot of sources, calibration, zones,
perception freshness, multiview groups, saved queries, dashboards, alerts, and readiness,
then `list_workflows()` / `get_workflow(name)` to route the job it was asked to do. Zone
geometry from camera evidence is previewed and approved before it is stored; perception is
checked for reuse before any worker is started; and threshold words are mapped to exact
operators. See [the agent operating surface](docs/agent-surface.md) for the full tool list,
the `/api/v1/agent/*` endpoints behind it, and the compatibility mode that re-advertises the
59 superseded low-level tools.

For a local stdio MCP client:

```powershell
$env:MANYSIGHT_URL = "http://127.0.0.1:8000"
python mcp_server/server.py
```

[`codex.config.example.toml`](codex.config.example.toml) shows one client-specific
configuration. The same MCP server can use Streamable HTTP for remote deployments;
see [Development and deployment](docs/development.md).

## Documentation

- [Architecture and current scope](docs/architecture.md)
- [The agent operating surface](docs/agent-surface.md)
- [Source connections and credentials](docs/source-connections.md)
- [Workers and observations](docs/workers.md)
- [Geometry and calibration](docs/geometry.md)
- [Multiview current state](docs/multiview.md)
- [Saved queries and generated dashboards](docs/queries-and-dashboards.md)
- [Guided four-camera demo](docs/guided-demo.md)
- [Workspace reinitialization and revisions](docs/workspace-reinitialization.md)
- [Alerts](docs/alerts.md)
- [Development and deployment](docs/development.md)
- [Agent playbooks](skills/README.md)
- [Agent-operability evaluation](evals/agent_operability/README.md)
- [Contributing](CONTRIBUTING.md)
- [Security policy](SECURITY.md)

The interactive API and `/openapi.json` are the authoritative REST contract.
`GET /api/v1/observations/contract` describes the worker payload at runtime.

## Development

```powershell
python -m pytest -q
npm test --prefix dashboard
npm run build --prefix dashboard
```

No separate formatter, linter, or type-check command is configured in the current
repository. More setup, environment variables, demo tooling, and test commands are
documented in [docs/development.md](docs/development.md).

## Contributing

Bug reports and pull requests are welcome; read [CONTRIBUTING.md](CONTRIBUTING.md)
before starting work. Do not include camera credentials, source URLs containing
credentials, recordings, database files, or other sensitive site data in issues or
commits.

## License

ManySight is released under the [Apache License 2.0](LICENSE).

The guided-demo runtime media is third-party NVIDIA sample media redistributed with
maintainer-confirmed permission. Its source and scope are documented in
[`demo/assets/guided_demo/README.md`](demo/assets/guided_demo/README.md); it is not
relicensed under Apache-2.0.
