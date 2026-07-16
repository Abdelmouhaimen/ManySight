"""StoreLens MCP server — the bridge that lets Codex (or any MCP client) operate the platform.

Run standalone:            python mcp_server/server.py
Register with Codex CLI:   see codex.config.example.toml at the repo root.

Env:
  STORELENS_URL      base URL of the platform (default http://localhost:8000)
  STORELENS_API_KEY  only if the server enforces one
  STORELENS_SKILLS   path to the skills/ folder (default: sibling of this file's parent)
"""
import json
import os
import urllib.parse
import urllib.request

from mcp.server.fastmcp import FastMCP, Image

BASE = os.environ.get("STORELENS_URL", "http://localhost:8000").rstrip("/")
API_KEY = os.environ.get("STORELENS_API_KEY", "")
SKILLS_DIR = os.environ.get(
    "STORELENS_SKILLS",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "skills"),
)

mcp = FastMCP(
    "storelens",
    instructions=(
        "StoreLens is the analysis workbench behind the ManySight retail operations POC. "
        "Cameras, a floor plan "
        "with named zones, and per-camera homographies (pixels -> floor meters) are configured "
        "by the store owner in the UI. You are the analysis brain: read sources and the map, "
        "run CV models on the streams, and post events back. Always call list_skills() first "
        "when asked for a new kind of analysis — skills are step-by-step recipes for common "
        "tasks (heatmaps, dwell time, state monitoring, alerts)."
    ),
)


def _req(method: str, path: str, body: dict | None = None, raw: bool = False):
    url = BASE + "/api/v1" + path
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if API_KEY:
        headers["X-API-Key"] = API_KEY
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=30) as res:
        payload = res.read()
        return payload if raw else json.loads(payload)


@mcp.tool()
def list_sources() -> list[dict]:
    """List every camera source: id, name, protocol kind, status, whether it is placed on the
    store map and calibrated (pixel->meter homography available)."""
    return _req("GET", "/sources")


@mcp.tool()
def get_source(source_id: int) -> dict:
    """Full detail for one source including credentials and the resolved connect_url you can
    open with OpenCV (cv2.VideoCapture), plus its calibration (homography H, error) if set."""
    return _req("GET", f"/sources/{source_id}?secrets=true")


@mcp.tool()
def get_snapshot(source_id: int) -> Image:
    """Latest frame from a source as an image — look at it to understand what the camera sees
    (angle, coverage, which zones are visible) before choosing models or drawing polygons."""
    data = _req("GET", f"/sources/{source_id}/snapshot.jpg", raw=True)
    fmt = "png" if data[:4] == b"\x89PNG" else "jpeg"
    return Image(data=data, format=fmt)


@mcp.tool()
def refresh_snapshot(source_id: int) -> dict:
    """Ask the platform to capture a fresh frame from the source right now (tests connectivity)."""
    return _req("POST", f"/sources/{source_id}/snapshot")


@mcp.tool()
def get_store_map() -> dict:
    """The store floor plan: name, dimensions in meters, wall polylines, text labels, all zones
    (named polygons with a semantic type: checkout/entrance/fridge/aisle/...), and every placed
    camera with its position, rotation, FOV and calibration state."""
    store = _req("GET", "/store")
    store["zones"] = _req("GET", "/zones")
    store["cameras"] = [
        {k: s[k] for k in ("id", "name", "kind", "status", "placement", "calibrated")}
        for s in _req("GET", "/sources")
    ]
    return store


@mcp.tool()
def list_zones() -> list[dict]:
    """All named zones as polygons in map meters. Zone ids are what events reference."""
    return _req("GET", "/zones")


@mcp.tool()
def project_points(source_id: int, points: list[dict]) -> dict:
    """Project camera pixel coordinates to store-map meters using the source's homography.
    points: [{"x": px, "y": px}, ...]. Prefer sending point_px directly in events instead —
    the platform projects automatically on ingest."""
    return _req("POST", f"/sources/{source_id}/project", {"points": points})


@mcp.tool()
def register_job(name: str, description: str = "", source_ids: list[int] | None = None,
                 event_types: list[str] | None = None) -> dict:
    """Register an analysis job BEFORE posting events. Returns the job with its id; pass that
    job_id to submit_events so the platform can attribute and monitor your analysis."""
    return _req("POST", "/jobs", {
        "name": name, "description": description,
        "source_ids": source_ids or [], "event_types": event_types or [],
    })


@mcp.tool()
def submit_events(events: list[dict], job_id: int | None = None) -> dict:
    """Post a batch of analysis events (max 5000). Event fields:
      ts (epoch seconds, optional), source_id, event_type (detection|zone_enter|zone_exit|
      zone_dwell|transition|state_change|count|custom), track_id, point_px {x,y} OR
      point_map {x,y} OR bbox [x,y,w,h], zone_id or zone (name), value (number, e.g. dwell
      seconds), label (e.g. state name), attributes (free dict, e.g. {"gender":"female"}).
    The platform auto-projects point_px to map meters (if the source is calibrated) and
    auto-assigns the zone containing the map point. Returns enrichment counts."""
    return _req("POST", "/events", {"job_id": job_id, "events": events})


@mcp.tool()
def get_events(since: float | None = None, until: float | None = None,
               event_type: str | None = None, zone_id: int | None = None,
               source_id: int | None = None, job_id: int | None = None,
               limit: int = 100) -> dict:
    """Query stored events (newest first) — use it to sanity-check what your job posted."""
    params = {k: v for k, v in {"since": since, "until": until, "event_type": event_type,
                                "zone_id": zone_id, "source_id": source_id, "job_id": job_id,
                                "limit": limit}.items() if v is not None}
    return _req("GET", "/events?" + urllib.parse.urlencode(params))


@mcp.tool()
def get_analytics(kind: str, params: dict | None = None) -> dict:
    """Read the platform's computed analytics. kind: summary | heatmap | dwell | occupancy |
    counts | transitions | states. params are the endpoint's query params (since/until epoch seconds,
    group_by, zone_id, ...). Useful to verify your events produce sensible insights."""
    allowed = {"summary", "heatmap", "dwell", "occupancy", "counts", "transitions", "states"}
    if kind not in allowed:
        raise ValueError(f"kind must be one of {sorted(allowed)}")
    qs = urllib.parse.urlencode(params or {})
    return _req("GET", f"/analytics/{kind}" + (f"?{qs}" if qs else ""))


@mcp.tool()
def create_alert_rule(name: str, kind: str, params: dict, webhook_url: str = "",
                      cooldown_s: float = 60) -> dict:
    """Create an alert rule evaluated on every ingested batch. kinds:
      dwell_exceeds {zone_id, seconds} · occupancy_exceeds {zone_id?, count, window_s} ·
      state_alert {label, source_id?, min_seconds?} · event_match {event_type, zone_id?,
      attr_key?, attr_value?}. Fired alerts appear in the UI and POST to webhook_url (n8n etc.)."""
    return _req("POST", "/alert-rules", {"name": name, "kind": kind, "params": params,
                                         "webhook_url": webhook_url, "cooldown_s": cooldown_s})


@mcp.tool()
def list_skills() -> list[dict]:
    """List the analysis skills (playbooks) shipped with the platform. Each skill is a
    step-by-step recipe for a task: heatmap, dwell-time, state-monitoring, alerts-workflows.
    Call get_skill(name) and follow it when the user asks for that kind of analysis."""
    out = []
    if os.path.isdir(SKILLS_DIR):
        for entry in sorted(os.listdir(SKILLS_DIR)):
            path = os.path.join(SKILLS_DIR, entry, "SKILL.md")
            if os.path.isfile(path):
                with open(path, encoding="utf-8") as f:
                    first = f.readline().strip().lstrip("# ")
                out.append({"name": entry, "title": first})
    return out


@mcp.tool()
def get_skill(name: str) -> str:
    """Return the full markdown playbook for a skill, including runnable worker-script
    templates. Follow it step by step; adapt the template to the user's exact request."""
    path = os.path.join(SKILLS_DIR, name, "SKILL.md")
    if not os.path.isfile(path):
        raise ValueError(f"unknown skill '{name}' — call list_skills() first")
    with open(path, encoding="utf-8") as f:
        return f.read()


if __name__ == "__main__":
    mcp.run()
