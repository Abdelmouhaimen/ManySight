"""StoreLens — spatial and temporal analytics from locally observed evidence.

Run:  uvicorn server.app:app --host 0.0.0.0 --port 8000
UI:   http://localhost:8000        API docs: http://localhost:8000/docs
Auth: optional — set STORELENS_API_KEY to require X-API-Key on /api/*.
The query-string key remains available for browser SSE compatibility; headers are
preferred for other clients.
"""
import asyncio
import contextlib
import logging
import os
import sys

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from . import db
from .platform_config import resolve as resolve_platform_config
from .routers import agent_ops, alerts, analytics, analytics_query, analyses, calibrations, dashboards, demo, events, geometry, jobs, multiview, observations, queries, sources, store, workspace, zones
from .services import alert_engine, config_cache, current_state, demo_media, demo_runtime, multiview as multiview_service, realtime
from .services.metrics import registry as metrics_registry
from .services.sse import broker

ALERT_POLL_INTERVAL_S = float(os.environ.get("STORELENS_ALERT_POLL_INTERVAL_S", "15"))


async def _alert_poll_loop():
    """Periodic, ingestion-independent evaluation of ongoing alert conditions
    (loiter, occupancy, state duration, unified analysis conditions) — see
    services/alert_engine.py:evaluate_ongoing. Runs for the life of the process;
    a failure in one tick is logged and never kills the loop."""
    while True:
        try:
            demo_runtime.cleanup_expired()
            # Also drains any live group tick the scheduler has not run yet, so
            # ongoing conditions are evaluated against current fused state.
            multiview_service.refresh_freshness(db.now())
            alerts_fired = alert_engine.evaluate_ongoing(db.now(), config_cache.zone_names())
            for a in alerts_fired:
                broker.publish("alert", a)
                broker.publish("alert.created", a)
        except Exception as exc:  # never let a transient DB/query error kill the poller
            print(f"periodic alert evaluation failed: {exc}", file=sys.stderr, flush=True)
        await asyncio.sleep(ALERT_POLL_INTERVAL_S)


@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI):
    try:
        demo_runtime.resume_active_sessions()
        demo_runtime.resume_promoted_media()
    except Exception:
        logging.getLogger("storelens.demo").exception("demo runtime recovery failed")
    # Live bookkeeping is in-process, so a restart rebuilds it from the persisted
    # current samples and marks every group for one reconciliation tick. Cameras
    # do not have to send a new frame for fused state to become coherent again.
    model = realtime.execution_model()
    if model["warning"]:
        logging.getLogger("storelens.realtime").warning(model["warning"])
    try:
        realtime.coordinator.reconcile()
    except Exception:
        logging.getLogger("storelens.realtime").exception("live state reconciliation failed")
    realtime.coordinator.start()
    task = asyncio.create_task(_alert_poll_loop())
    try:
        yield
    finally:
        await realtime.coordinator.stop()
        await demo_runtime.shutdown()
        demo_media.stop()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


app = FastAPI(
    title="ManySight",
    version="1.0.0",
    description="Infrastructure for turning raw camera and sensor observations into spatial and "
                "temporal analytics. StoreLens manages logical sources, protected connection configuration, mapped "
                "geometry, heartbeat-backed workers, schema-v2 detection/measurement/state observations, derived "
                "current/fused state, saved queries, generated dashboards, and alerts. Local workers open sources and run models; the platform "
                "does not proxy operational feeds or execute worker code. An optional isolated guided demo presents allowlisted local media "
                "and a provenance-hashed cache derived offline through the real StoreLens pipeline on one synchronized replay clock.",
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


@app.middleware("http")
async def demo_workspace_guard(request: Request, call_next):
    """Route explicit demo-session API requests to their isolated workspace."""
    session_id = request.headers.get("x-storelens-demo-session") or request.query_params.get("demo_session")
    if session_id and request.url.path.startswith("/api/v1/") \
            and not request.url.path.startswith("/api/v1/demo/"):
        workspace_path = demo_runtime.session_database(session_id)
        if workspace_path is None:
            return JSONResponse({"detail": "demo session is not active"}, status_code=409)
        with db.using_database(workspace_path):
            return await call_next(request)
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
    allow_headers=["Authorization", "Content-Type", "X-API-Key", "X-StoreLens-Credential-Key",
                   "X-StoreLens-Demo-Session", "MCP-Protocol-Version"],
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
        "guided_demo_assets_available": demo_runtime.asset_status()["available"],
        "demo_stream_supervisor": demo_media.status(),
    }


@app.get("/api/v1/realtime/metrics", tags=["platform"])
def realtime_metrics():
    """Process-local pipeline instrumentation: ingestion, live scheduler, fusion.

    Observability only — nothing in the pipeline reads these back. Counters are
    cumulative since process start and durations are percentiles over a bounded
    ring of recent samples, so this endpoint is safe to poll during a load test.
    `raw_evidence_dropped` is structural: nothing can increment it.
    """
    snapshot = metrics_registry.snapshot()
    return {
        "coordinator": realtime.coordinator.status(),
        "execution_model": realtime.execution_model(),
        "raw_evidence_dropped": 0,
        **snapshot,
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

StoreLens is an observation and analytics platform. It never opens or proxies an
operational camera feed and never runs computer-vision models. Its optional guided demo
serves only allowlisted local sample media and a cache derived offline through the real
platform pipeline; runtime is neither a worker, live inference, nor live fusion.
StoreLens can keep source credentials encrypted for
explicitly privileged worker resolution; the worker still opens the feed locally and posts
raw observations over HTTPS.

## Endpoints

- Remote MCP: `{mcp_url}`
- OpenAPI: `{endpoints["openapi_url"]}`
- Interactive API: `{endpoints["docs_url"]}`
- REST base: `{endpoints["rest_url"]}`
- Health: `{endpoints["health_url"]}`
- Runtime endpoint registry: `{endpoints["platform_config_url"]}`

## Start here

1. `GET {endpoints["rest_url"]}/agent/workspace` — one snapshot of sources, calibration,
   zones, perception freshness, multiview groups, saved queries, dashboards, alert rules,
   and readiness. Over MCP this is `inspect_workspace()`. It never contains credentials.
2. `GET {endpoints["rest_url"]}/agent/workflows` then `.../agent/workflows/{{name}}` — route
   the job you were asked to do to its prerequisites, sequence, invariants, and tools.
3. Load the named skill (`get_skill` over MCP), starting with `storelens-core`.
4. Verify with real reads before reporting success.

The MCP endpoint advertises a curated set of semantic tools. REST and the SDK remain the
complete low-level interface; `/openapi.json` is authoritative.

## Default workflow

1. Reuse the requested logical source, or create one with either `storelens_managed`
   structured connection details or an `external_secret` locator with `local_secret_ref`.
2. Place and calibrate the source before any geometry or fusion work. When a named region
   has no canonical zone, inspect the calibrated cameras first
   (`GET {endpoints["rest_url"]}/agent/sources/{{id}}/frame-capture-plan`, run locally),
   then `POST {endpoints["rest_url"]}/agent/zone-preview`, get the user's approval, and
   only then `POST {endpoints["rest_url"]}/agent/zone-commit`.
3. `GET {endpoints["rest_url"]}/agent/perception` before starting any worker — reuse healthy
   perception instead of starting a duplicate. A stale or missing source is unknown, not zero.
4. `GET {endpoints["rest_url"]}/agent/worker-recipe` for the CURRENT submission contract.
   Never infer it from an example, demo, or older worker script found in a repository.
5. Resolve camera access in the worker with the privileged source-connection endpoint or an
   external environment/keychain reference. Normal source reads never reveal secrets.
6. Register a job, then register a worker and heartbeat every 5-15 seconds, reporting
   `local_fps` and `submission_hz` in metrics.
7. For every processed detection frame, submit one atomic
   `POST {endpoints["rest_url"]}/detection-samples` envelope with one exact timestamp,
   opaque `sample_id`, and zero or more detections. `detections=[]` is a known empty frame.
   Prefer the SDK sample builder; never create a fake detection for an empty frame. Local
   detection may run at full camera FPS while central submission runs slower. Legacy
   detection rows completed by `detection_frame_count` remain compatible. Only three
   observation kinds exist — `detection`, `measurement`, `state`; never send zone_id/zone or
   compute zone entry/exit, dwell, occupancy, movement, or a state change. See
   `GET {endpoints["rest_url"]}/observations/contract`.
8. Verify with `GET {endpoints["rest_url"]}/agent/perception`,
   `GET {endpoints["rest_url"]}/observations/latest-frames`, and
   `GET {endpoints["rest_url"]}/multiview/current`. Preview a deterministic query, save it
   with `POST {endpoints["rest_url"]}/queries`, and reference it from a generated dashboard
   only when requested. Agents never receive SQL access. Threshold words are exact: "more
   than 2" is `> 2` and "at least 2" is `>= 2`.

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
- First call for an agent task: {endpoints["rest_url"]}/agent/workspace
- Workflow index: {endpoints["rest_url"]}/agent/workflows
- Current worker contract: {endpoints["rest_url"]}/agent/worker-recipe
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
        "agent_workspace": endpoints["rest_url"] + "/agent/workspace",
        "agent_workflows": endpoints["rest_url"] + "/agent/workflows",
        "mcp": {"transport": "streamable-http", "url": endpoints["mcp_url"]},
        "openapi": endpoints["openapi_url"],
        "docs": endpoints["docs_url"],
        "rest_base": endpoints["rest_url"],
        "camera_access": "worker_local",
        "platform_config": endpoints["platform_config_url"],
        "endpoint_profile": endpoints["profile"],
    }


for r in (sources, store, zones, geometry, calibrations, multiview, jobs, events,
         observations, analytics, analytics_query, queries, dashboards, analyses, alerts,
         workspace, demo, agent_ops):
    app.include_router(r.router, prefix="/api/v1")

_server_dir = os.path.dirname(__file__)
_dashboard_dist = os.path.join(os.path.dirname(_server_dir), "dashboard", "dist")

if not os.path.isdir(_dashboard_dist):
    raise RuntimeError("The bundled dashboard build is missing. Run `npm install --prefix dashboard` and "
                       "`npm run build --prefix dashboard` from the repository root.")

app.mount("/", StaticFiles(directory=_dashboard_dist, html=True), name="ui")
