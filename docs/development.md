# Development and deployment

## Prerequisites and installation

ManySight currently targets Python 3.11+ and Node.js 20+. From the repository root:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt -r requirements-test.txt
npm install --prefix dashboard
npm run build --prefix dashboard
```

On macOS or Linux use `source .venv/bin/activate`. OpenCV is included because the
Python SDK and public camera examples can open video; the platform server itself does
not perform computer vision.

## Run the platform

```powershell
python -m uvicorn server.app:app --host 127.0.0.1 --port 8000
```

The server requires `dashboard/dist`, which is produced by the dashboard build. On
macOS or Linux, `./run.sh` is a convenience wrapper that binds to `0.0.0.0` and reads
the optional `PORT` variable.

For dashboard development, run the API and Vite separately:

```powershell
npm run dev --prefix dashboard
```

The endpoint registry in `config/endpoints.json` defines local and hosted profiles.
`GET /api/v1/platform-config` returns the active public URLs without exposing the
registry file path.

## Configuration

| Variable | Purpose |
|---|---|
| `STORELENS_DATA` | Data directory; defaults to `./data` and contains `storelens.db`. |
| `STORELENS_API_KEY` | Optional API key for `/api/*`; clients normally send `X-API-Key`. Browser SSE and protected media URLs use the compatibility query parameter. |
| `STORELENS_PUBLIC_READS` | Allows unauthenticated read requests when true; credential resolution remains protected. |
| `STORELENS_CREDENTIAL_KEY` | URL-safe base64 encoding of exactly 32 random bytes for managed credential encryption. |
| `STORELENS_CREDENTIAL_ACCESS_KEY` | Header-only key for privileged source-connection resolution; falls back to the API key if omitted. |
| `STORELENS_ALERT_POLL_INTERVAL_S` | Periodic alert evaluation interval; defaults to 15 seconds. |
| `STORELENS_LIVE_TICK_INTERVAL_S` | Maximum live fusion cadence; defaults to 0.01 s (100 Hz). A tick only runs for groups with new source state. |
| `STORELENS_LIVE_SCHEDULER` | `0` disables the background fusion scheduler; reads still drain pending ticks. |
| `STORELENS_SQLITE_SYNCHRONOUS` | `NORMAL` (default) or `FULL`. See [the realtime pipeline](realtime-pipeline.md) for what each guarantees and what FULL costs. |
| `STORELENS_INLINE_INGEST_MAX_OBSERVATIONS` | Batches at or below this size are processed on the event loop; larger ones go to the pipeline thread. Defaults to 64. |
| `STORELENS_INLINE_INGEST_LOCK_WAIT_S` | How long an inline batch waits for the write lock before offloading itself; defaults to 0.25 s. |
| `STORELENS_DEMO_ASSET_DIR` | Optional local path to extracted NVIDIA `datasets/mtmc_12cam` assets. The demo uses cameras 1-4. |
| `STORELENS_DEMO_STREAM_PORT` | Loopback port for the post-promotion synchronized demo stream supervisor; defaults to 8765. |
| `STORELENS_ENDPOINT_CONFIG` | Optional path to an endpoint registry JSON file. |
| `STORELENS_ENDPOINT_PROFILE` | Endpoint profile name; defaults to the registry's active profile. |
| `STORELENS_PUBLIC_URL` | Public platform URL advertised by discovery endpoints. |
| `STORELENS_PUBLIC_MCP_URL` | Public Streamable HTTP MCP URL. |
| `STORELENS_CORS_ORIGINS` | Comma-separated browser origins. |
| `STORELENS_URL` | Platform base URL used by the MCP process. |
| `STORELENS_REST_URL` | Optional REST-base override used by MCP. |
| `STORELENS_SKILLS` | Optional MCP skill-directory override. |
| `STORELENS_MCP_TRANSPORT` | `stdio` (default) or `streamable-http`. |
| `STORELENS_MCP_HOST` / `STORELENS_MCP_PORT` | Streamable HTTP bind settings; defaults to `127.0.0.1:8001`. |
| `STORELENS_MCP_ALLOWED_HOSTS` / `STORELENS_MCP_ALLOWED_ORIGINS` | Streamable HTTP host/origin allowlists. |
| `STORELENS_MCP_LEGACY_TOOLS` | `1` also advertises the 59 deprecated low-level MCP tools alongside the 18 curated ones. Migration path only; see [the agent operating surface](agent-surface.md). |

Do not place secrets in tracked files or command-line query parameters. The dashboard
uses a query-string API key only where browser APIs cannot attach a header; avoid this
compatibility path for ordinary clients because URLs are more likely to be logged. An
API key is optional rather than a complete production authentication system; deployments are
responsible for TLS, network controls, key rotation, backups, and process isolation.

## MCP transports

Local clients normally launch `python mcp_server/server.py` over stdio. To run the
separate Streamable HTTP service:

```powershell
$env:STORELENS_MCP_TRANSPORT = "streamable-http"
$env:STORELENS_MCP_HOST = "127.0.0.1"
$env:STORELENS_MCP_PORT = "8001"
python mcp_server/server.py
```

Configure authentication and allowed hosts/origins before exposing that service beyond
the loopback interface.

## Tests and validation

```powershell
python -m pytest -q
npm test --prefix dashboard
npm run build --prefix dashboard
```

There is no configured repository-wide formatter, linter, static type checker, or
documentation build at present. The test suite uses temporary databases and restores
environment state through `tests/conftest.py`.

Performance is measured separately, because it needs a running server and takes
minutes rather than seconds:

```powershell
python scripts/load_test_realtime.py --cameras 4 --fps 60 --duration 30
python scripts/load_test_realtime.py --scenario asymmetric
python scripts/load_test_realtime.py --scenario stop-camera
python scripts/load_test_realtime.py --scenario overload
```

Each run reports sustained input, live-scheduler behaviour, latency percentiles, and
verifies that every accepted sample is durably stored; see
[the realtime pipeline](realtime-pipeline.md).

After starting the server, verify `/`, `/docs`, `/openapi.json`,
`/api/v1/platform-config`, and `/api/v1/health`. `scripts/smoke_test.sh` is a macOS/Linux
API smoke check for a running development server.

## Demo data and local video

`python scripts/seed_demo.py` replaces workspace, geometry, sources, alert rules,
historical alerts, and non-migrated saved queries with synthetic data, then adds synthetic
observation history and jobs. It is destructive to the selected `STORELENS_DATA`
database and must not be used against retained data.

`demo/loop_video_stream.py` loops a local video as an MJPEG endpoint for development.
It is not a camera gateway or a required ManySight service. See `demo/README.md`.

The separate [guided demo](guided-demo.md) uses an isolated database and a committed
numerical replay fixture. Its NVIDIA media is downloaded on demand and remains ignored.
The four-camera synchronized MJPEG supervisor starts only after setup promotion.
