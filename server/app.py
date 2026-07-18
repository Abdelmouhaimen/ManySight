"""StoreLens — agent-powered computer-vision analytics for physical spaces.

Run:  uvicorn server.app:app --host 0.0.0.0 --port 8000
UI:   http://localhost:8000        API docs: http://localhost:8000/docs
Auth: optional — set STORELENS_API_KEY to require X-API-Key (or ?api_key=) on /api/*.
"""
import os

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from . import db
from .platform_config import resolve as resolve_platform_config
from .routers import alerts, analytics, events, geometry, insights, jobs, sources, store, zones

app = FastAPI(
    title="StoreLens",
    version="1.0.0",
    description="POC infrastructure for ManySight physical-space intelligence: logical observation sources, global map "
                "zones, floor and named-plane localization, camera decision ROIs, a generic evidence-rich "
                "event stream, heartbeat-backed workers, reviewable insights, and alerts. "
                "AI agents connect through MCP, access cameras locally, run models, and post observations back here.",
)

db.init_db()

CORS_ORIGINS = resolve_platform_config()["cors_origins"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-API-Key", "MCP-Protocol-Version"],
)

API_KEY = os.environ.get("STORELENS_API_KEY", "")
PUBLIC_READS = os.environ.get("STORELENS_PUBLIC_READS", "false").lower() in {"1", "true", "yes"}


@app.middleware("http")
async def api_key_guard(request: Request, call_next):
    if API_KEY and request.url.path.startswith("/api/") and request.url.path != "/api/v1/health":
        if PUBLIC_READS and request.method in {"GET", "HEAD", "OPTIONS"}:
            return await call_next(request)
        supplied = request.headers.get("x-api-key") or request.query_params.get("api_key")
        if supplied != API_KEY:
            return JSONResponse({"detail": "invalid or missing API key"}, status_code=401)
    return await call_next(request)


@app.get("/api/v1/health")
def health():
    return {
        "ok": True,
        "service": "storelens",
        "ts": db.now(),
        "auth_required": bool(API_KEY),
        "public_reads": PUBLIC_READS,
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

StoreLens is a hosted observation and insight platform. It never opens a camera feed,
stores camera credentials, or runs computer-vision models. The agent runs the worker on
the device or edge gateway that can reach the source and posts raw observations over HTTPS.

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
   A source locator may contain `device_index` or `local_secret_ref`, but never a URL,
   username, password, token, or API key.
3. Resolve camera access locally. For example, a webcam locator with `device_index: 0`
   means OpenCV `VideoCapture(0)` on the worker device.
4. Register a job, then register a worker and heartbeat every 5-15 seconds.
5. Post raw observations to `POST {endpoints["rest_url"]}/events`. For a people-count chart, post one
   `count` event per sampling interval with `source_id`, `label: "person"`, and the
   current per-frame `value`; do not post a cumulative or time-aggregated total.
6. Verify events and analytics, then register an insight definition.

Camera credentials stay in a local environment variable, keychain, or ignored worker
configuration. StoreLens source metadata is provenance and coordination, not a stream proxy.
"""


@app.get("/llms.txt", response_class=PlainTextResponse, include_in_schema=False)
def llms_index(request: Request):
    endpoints = _endpoint_config(request)
    return f"""# StoreLens
> Agent-operated computer-vision observation and insight platform.

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
        "camera_access": "agent_local",
        "platform_config": endpoints["platform_config_url"],
        "endpoint_profile": endpoints["profile"],
    }


for r in (sources, store, zones, geometry, jobs, events, analytics, alerts, insights):
    app.include_router(r.router, prefix="/api/v1")

_server_dir = os.path.dirname(__file__)
_dashboard_dist = os.path.join(os.path.dirname(_server_dir), "dashboard", "dist")

if not os.path.isdir(_dashboard_dist):
    raise RuntimeError("ManySight dashboard build is missing. Run `npm install && npm run build` in dashboard/.")

app.mount("/", StaticFiles(directory=_dashboard_dist, html=True), name="ui")
