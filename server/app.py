"""StoreLens — spatial and temporal analytics from locally observed evidence.

Run:  uvicorn server.app:app --host 0.0.0.0 --port 8000
UI:   http://localhost:8000        API docs: http://localhost:8000/docs
Auth: optional — set STORELENS_API_KEY to require X-API-Key on /api/*.
The query-string key remains available for browser SSE compatibility; headers are
preferred for other clients.
"""
import asyncio
import contextlib
import os
import sys

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from . import db
from .platform_config import resolve as resolve_platform_config
from .routers import alerts, analytics, analytics_query, analyses, calibrations, dashboards, events, geometry, jobs, multiview, observations, queries, sources, store, zones
from .services import alert_engine, current_state, multiview as multiview_service
from .services.sse import broker

ALERT_POLL_INTERVAL_S = float(os.environ.get("STORELENS_ALERT_POLL_INTERVAL_S", "15"))


async def _alert_poll_loop():
    """Periodic, ingestion-independent evaluation of ongoing alert conditions
    (loiter, occupancy, state duration, unified analysis conditions) — see
    services/alert_engine.py:evaluate_ongoing. Runs for the life of the process;
    a failure in one tick is logged and never kills the loop."""
    while True:
        try:
            multiview_service.refresh_freshness(db.now())
            zone_names = {z["id"]: z["name"] for z in db.q("SELECT id, name FROM zones")}
            alerts_fired = alert_engine.evaluate_ongoing(db.now(), zone_names)
            for a in alerts_fired:
                broker.publish("alert", a)
                broker.publish("alert.created", a)
        except Exception as exc:  # never let a transient DB/query error kill the poller
            print(f"periodic alert evaluation failed: {exc}", file=sys.stderr, flush=True)
        await asyncio.sleep(ALERT_POLL_INTERVAL_S)


@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI):
    task = asyncio.create_task(_alert_poll_loop())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


app = FastAPI(
    title="StoreLens",
    version="1.0.0",
    description="Infrastructure for turning raw camera and sensor observations into spatial and "
                "temporal analytics. StoreLens manages logical sources, protected connection configuration, mapped "
                "geometry, heartbeat-backed workers, schema-v2 detection/measurement/state observations, derived "
                "current/fused state, saved queries, generated dashboards, and alerts. Local workers open sources and run models; the platform "
                "does not proxy feeds or execute worker code.",
    lifespan=lifespan,
)

db.init_db()
current_state.rebuild_from_history()

API_KEY = os.environ.get("STORELENS_API_KEY", "")
PUBLIC_READS = os.environ.get("STORELENS_PUBLIC_READS", "false").lower() in {"1", "true", "yes"}


@app.middleware("http")
async def api_key_guard(request: Request, call_next):
    if API_KEY and request.url.path.startswith("/api/") and request.url.path != "/api/v1/health":
        # The connection-resolution endpoint has its own stronger, header-only
        # credential access check. Do not make a distinct credential key also
        # satisfy the general API-key middleware.
        if request.method == "GET" and request.url.path.startswith("/api/v1/sources/") \
                and request.url.path.endswith("/connection"):
            return await call_next(request)
        if PUBLIC_READS and request.method in {"GET", "HEAD", "OPTIONS"}:
            return await call_next(request)
        supplied = request.headers.get("x-api-key") or request.query_params.get("api_key")
        if supplied != API_KEY:
            return JSONResponse({"detail": "invalid or missing API key"}, status_code=401)
    return await call_next(request)


# Added after api_key_guard so it becomes the outermost middleware (Starlette
# runs the last-registered middleware first) — otherwise a cross-origin CORS
# preflight (OPTIONS, no X-API-Key) gets 401'd by the guard before CORS
# headers are ever attached, breaking every private, key-protected deployment.
CORS_ORIGINS = resolve_platform_config()["cors_origins"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-API-Key", "X-StoreLens-Credential-Key", "MCP-Protocol-Version"],
)


@app.get("/api/v1/health")
def health():
    return {
        "ok": True,
        "service": "storelens",
        "ts": db.now(),
        "auth_required": bool(API_KEY),
        "public_reads": PUBLIC_READS,
        "managed_credentials_configured": bool(os.environ.get("STORELENS_CREDENTIAL_KEY")),
        "credential_access_configured": bool(os.environ.get("STORELENS_CREDENTIAL_ACCESS_KEY") or API_KEY),
        "endpoint_profile": resolve_platform_config()["profile"],
    }


def _endpoint_config(request: Request) -> dict:
    return resolve_platform_config(str(request.base_url).rstrip("/"))


@app.get("/api/v1/platform-config", tags=["platform"])
def platform_config(request: Request):
    resolved = _endpoint_config(request)
    return {key: value for key, value in resolved.items() if key != "config_path"}


@app.get("/agent.md", response_class=PlainTextResponse, include_in_schema=False)
@app.get("/agend.md", response_class=PlainTextResponse, include_in_schema=False)
def agent_guide(request: Request):
    endpoints = _endpoint_config(request)
    mcp_url = endpoints["mcp_url"]
    return f"""# Use StoreLens from an agent

StoreLens is an observation and analytics platform. It never opens or proxies a camera
feed and never runs computer-vision models. It can keep source credentials encrypted for
explicitly privileged worker resolution; the worker still opens the feed locally and posts
raw observations over HTTPS.

## Endpoints

- Remote MCP: `{mcp_url}`
- OpenAPI: `{endpoints["openapi_url"]}`
- Interactive API: `{endpoints["docs_url"]}`
- REST base: `{endpoints["rest_url"]}`
- Health: `{endpoints["health_url"]}`
- Runtime endpoint registry: `{endpoints["platform_config_url"]}`

## Default workflow

1. Connect to the MCP endpoint and load the `storelens-platform` skill.
2. Call `list_sources`. Reuse the requested logical source or call `create_source`.
   Choose either `storelens_managed` structured connection details or an
   `external_secret` locator with `local_secret_ref`.
3. Resolve camera access in the worker with the privileged source-connection endpoint or
   an external environment/keychain reference. Normal source reads never reveal secrets.
4. Register a job, then register a worker and heartbeat every 5-15 seconds.
5. Submit only three observation kinds to `POST {endpoints["rest_url"]}/observations/batch`:
   `detection` (an observed entity with spatial evidence), `measurement` (an observed
   numeric value — e.g. one `value` per sampling interval, never a precomputed average
   or cumulative total), or `state` (an observed current categorical value, sent on
   every sample including repeats). Never resolve a zone or send zone_id/zone, and
   never compute zone entry/exit, dwell, occupancy, movement, or a state change —
   StoreLens derives all of those itself. See `GET {endpoints["rest_url"]}/observations/contract`.
6. For every processed person-detection frame, send its zero or more detections,
   then one `detection_frame_count` measurement including zero, all with one exact
   timestamp and one opaque `sample_id`. Prefer the SDK atomic sample builder. Never use a fake detection for an empty frame. Live scene state changes
   only when this newer completion marker arrives; freshness is reported separately.
7. Verify with `GET {endpoints["rest_url"]}/observations/latest`,
   `GET {endpoints["rest_url"]}/observations/latest-frames`, and
   `GET {endpoints["rest_url"]}/multiview/current`. Preview a deterministic query, save it
   with `POST {endpoints["rest_url"]}/queries`, and reference it from a generated dashboard
   only when requested. Agents never receive SQL access.

Managed credentials are encrypted at rest with `STORELENS_CREDENTIAL_KEY` and are returned
only by the header-authenticated connection endpoint. External-secret mode remains available.
In either mode StoreLens is provenance and coordination, not a stream proxy.
"""


@app.get("/llms.txt", response_class=PlainTextResponse, include_in_schema=False)
def llms_index(request: Request):
    endpoints = _endpoint_config(request)
    return f"""# StoreLens
> Spatial and temporal analytics from observations produced by local workers.

- Agent instructions: {endpoints["agent_guide_url"]}
- Runtime endpoint registry: {endpoints["platform_config_url"]}
- Remote MCP: {endpoints["mcp_url"]}
- OpenAPI schema: {endpoints["openapi_url"]}
- Interactive API documentation: {endpoints["docs_url"]}
"""


@app.get("/.well-known/storelens.json", include_in_schema=False)
def storelens_discovery(request: Request):
    endpoints = _endpoint_config(request)
    return {
        "name": "StoreLens",
        "version": app.version,
        "agent_instructions": endpoints["agent_guide_url"],
        "mcp": {"transport": "streamable-http", "url": endpoints["mcp_url"]},
        "openapi": endpoints["openapi_url"],
        "docs": endpoints["docs_url"],
        "rest_base": endpoints["rest_url"],
        "camera_access": "worker_local",
        "platform_config": endpoints["platform_config_url"],
        "endpoint_profile": endpoints["profile"],
    }


for r in (sources, store, zones, geometry, calibrations, multiview, jobs, events,
         observations, analytics, analytics_query, queries, dashboards, analyses, alerts):
    app.include_router(r.router, prefix="/api/v1")

_server_dir = os.path.dirname(__file__)
_dashboard_dist = os.path.join(os.path.dirname(_server_dir), "dashboard", "dist")

if not os.path.isdir(_dashboard_dist):
    raise RuntimeError("The bundled dashboard build is missing. Run `npm install --prefix dashboard` and "
                       "`npm run build --prefix dashboard` from the repository root.")

app.mount("/", StaticFiles(directory=_dashboard_dist, html=True), name="ui")
