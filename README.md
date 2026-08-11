# StoreLens

StoreLens is intended to be an open-source platform for turning observations from cameras and
sensors into spatial and temporal analytics for physical spaces. It stores source
configuration and mapped geometry, accepts raw observations from local workers,
projects spatial evidence into a floor plan, and derives visits, dwell, occupancy,
transitions, analyses, and alerts.

**StoreLens** is the platform, REST API, SDK, and MCP integration. **ManySight** is
the bundled web dashboard. The dashboard name is retained as a user-interface
brand; the repository and API use StoreLens terminology.

> **Release status:** this repository is under active development. It currently
> uses SQLite, represents mapped surfaces with planar homographies, and relies on
> external worker processes for inference. It does not yet contain an open-source
> license; see [License](#license).

## What StoreLens does

- Registers camera, video, file, and sensor sources with managed or external-secret
  connection configuration.
- Stores a metric floor plan, zones, camera placement, floor calibrations,
  projection surfaces, and per-camera zone views.
- Accepts schema-v2 `detection`, `measurement`, and `state` observations.
- Assigns zones and derives visits, dwell, occupancy, transitions, state intervals,
  saved analyses, and alerts from those observations.
- Exposes a web dashboard, REST/OpenAPI API, Python worker SDK, and MCP server.

StoreLens does not prescribe a computer-vision model or camera vendor, treat
anonymous tracker IDs as verified identities, infer cross-camera identity, proxy
camera feeds through the platform server, or run arbitrary worker code inside the
server process.

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
StoreLens
  geometry enrichment -> zone assignment -> temporal derivation
        |
        v
analytics / alerts / ManySight dashboard
```

Workers open sources where the device or stream is reachable. They submit direct
evidence, not StoreLens-owned results: no canonical zone IDs, entry/exit events,
dwell, occupancy, transitions, state changes, or dashboard aggregates. StoreLens
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

The default SQLite database is `data/storelens.db`. Use the dashboard's
**Setup** section to define the workspace, trace a floor plan, create zones, add
sources, and calibrate cameras.

## Source connections

A source combines a logical device with non-secret connection configuration.
Sensitive authentication material is stored or resolved separately:

- `storelens_managed` stores validated connection fields on the source and encrypts
  credentials in the database. An authorized worker resolves the connection through
  a dedicated, header-authenticated endpoint.
- `external_secret` stores only `locator.local_secret_ref`; the worker resolves that
  reference from its own environment, keychain, or ignored configuration.

Normal source discovery never returns credentials. StoreLens still does not connect
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
from storelens import StoreLens

client = StoreLens("http://127.0.0.1:8000", api_key="")
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
    # Replace these coordinates with output from your detector/tracker.
    client.submit_detection(
        source_id=source["id"],
        entity_id="track-1",
        entity_type="person",
        point_px=(320, 470),
        ts=time.time(),
    )
    client.flush_observations()
```

Run a real worker as a long-lived process, heartbeat every 5–15 seconds, and obey
cooperative stop/restart requests. The SDK lives at
[`sdk/python/storelens.py`](sdk/python/storelens.py); examples are in
[`examples/`](examples/). See [Workers and observations](docs/workers.md) before
writing a new integration.

## MCP

The MCP server is an agent-facing adapter over the REST API. It exposes platform
discovery, geometry, job/worker coordination, observations, analytics, alerts, and
the playbooks in [`skills/`](skills/). It does not process video itself.

For a local stdio MCP client:

```powershell
$env:STORELENS_URL = "http://127.0.0.1:8000"
python mcp_server/server.py
```

[`codex.config.example.toml`](codex.config.example.toml) shows one client-specific
configuration. The same MCP server can use Streamable HTTP for remote deployments;
see [Development and deployment](docs/development.md).

## Documentation

- [Architecture and current scope](docs/architecture.md)
- [Source connections and credentials](docs/source-connections.md)
- [Workers and observations](docs/workers.md)
- [Development and deployment](docs/development.md)
- [Agent playbooks](skills/README.md)
- [Contributing](CONTRIBUTING.md)
- [Security policy](SECURITY.md)

The interactive API and `/openapi.json` are the authoritative REST contract.
`GET /api/v1/observations/contract` describes the worker payload at runtime.

## Development

```powershell
python -m pytest -q
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

**No explicit open-source license is currently present.** Until the maintainers add
an approved license, copyright law reserves all rights and this repository should
not be described or redistributed as legally open source. Selecting and adding a
license is a release blocker.
