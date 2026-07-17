"""StoreLens — agent-powered computer-vision analytics for physical spaces.

Run:  uvicorn server.app:app --host 0.0.0.0 --port 8000
UI:   http://localhost:8000        API docs: http://localhost:8000/docs
Auth: optional — set STORELENS_API_KEY to require X-API-Key (or ?api_key=) on /api/*.
"""
import os

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import db
from .routers import alerts, analytics, events, geometry, insights, jobs, sources, store, zones

app = FastAPI(
    title="StoreLens",
    version="1.0.0",
    description="POC infrastructure for ManySight physical-space intelligence: camera sources, global map "
                "zones, floor and named-plane localization, camera decision ROIs, a generic evidence-rich "
                "event stream, heartbeat-backed workers, reviewable insights, and alerts. "
                "AI agents (Codex) connect via MCP, run models, and post events back here.",
)

db.init_db()

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

API_KEY = os.environ.get("STORELENS_API_KEY", "")


@app.middleware("http")
async def api_key_guard(request: Request, call_next):
    if API_KEY and request.url.path.startswith("/api/") and request.url.path != "/api/v1/health":
        supplied = request.headers.get("x-api-key") or request.query_params.get("api_key")
        if supplied != API_KEY:
            return JSONResponse({"detail": "invalid or missing API key"}, status_code=401)
    return await call_next(request)


@app.get("/api/v1/health")
def health():
    return {"ok": True, "service": "storelens", "ts": db.now(), "auth_required": bool(API_KEY)}


for r in (sources, store, zones, geometry, jobs, events, analytics, alerts, insights):
    app.include_router(r.router, prefix="/api/v1")

_server_dir = os.path.dirname(__file__)
_dashboard_dist = os.path.join(os.path.dirname(_server_dir), "dashboard", "dist")

if not os.path.isdir(_dashboard_dist):
    raise RuntimeError("ManySight dashboard build is missing. Run `npm install && npm run build` in dashboard/.")

app.mount("/", StaticFiles(directory=_dashboard_dist, html=True), name="ui")
