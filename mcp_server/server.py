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
        "tasks (heatmaps, dwell time, state monitoring, alerts). "
        "Post raw observations only (what the model saw), never computed aggregates — the "
        "platform derives dwell, durations, and every insight. After posting events, register "
        "the resulting view with register_insight so it appears in the Insights catalogue."
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
def create_zone(name: str, ztype: str = "area",
                polygon_map: list[dict] | None = None,
                polygon_px: list[dict] | None = None,
                source_id: int | None = None) -> dict:
    """Create a named zone from a polygon you propose — typically after looking at a
    get_snapshot frame ("mark the dashed-line area on cam 1 as restricted").
    Pass EITHER polygon_map ([{x,y}, ...] in floor meters) OR polygon_px ([{x,y}, ...]
    in that camera's pixels) + source_id — the platform projects pixels to the map
    through the source's calibrated homography (409 if uncalibrated: ask the user to
    calibrate, or compute map points yourself). ztype is a semantic label (restricted,
    checkout, entrance, queue, aisle, stockroom, equipment, hall, classroom,
    playground, meeting_room, area, custom) — it carries NO behavior: what happens
    when someone enters (alerts, review signals) is configured separately with
    create_alert_rule; your worker just posts zone_enter/zone_exit and never needs to
    know what the zone means. Confirm the polygon with the user before creating it."""
    return _req("POST", "/zones", {"name": name, "ztype": ztype, "polygon": polygon_map,
                                   "polygon_px": polygon_px, "source_id": source_id})


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
    """Post a batch of raw observations (max 5000). Event fields:
      ts (epoch seconds, optional), source_id, event_type (detection|zone_enter|zone_exit|
      transition|state_change|count|custom), track_id, point_px {x,y} OR point_map {x,y}
      OR bbox [x,y,w,h], zone_id or zone (name), value (a per-frame count sample only),
      label (e.g. state name), attributes (free dict, e.g. {"gender":"female"}).
    Contract: post what the model SAW, never computed aggregates. The platform derives
    dwell from zone_enter/zone_exit pairs and state durations from state_change
    timestamps. `zone_dwell` is deprecated: still stored, but its value is ignored by
    analytics and alerts. The platform auto-projects point_px to map meters (if the
    source is calibrated) and auto-assigns the zone containing the map point.
    Returns enrichment counts."""
    return _req("POST", "/events", {"job_id": job_id, "events": events})


@mcp.tool()
def get_events(since: float | None = None, until: float | None = None,
               event_type: str | None = None, zone_id: int | None = None,
               source_id: int | None = None, job_id: int | None = None,
               track_id: str | None = None, label: str | None = None,
               cursor: str | None = None, limit: int = 100) -> dict:
    """Query stored events (newest first) — use it to sanity-check what your job posted.
    Pass the returned next_cursor back as `cursor` to page through large result sets;
    `total` counts all rows matching the filters."""
    params = {k: v for k, v in {"since": since, "until": until, "event_type": event_type,
                                "zone_id": zone_id, "source_id": source_id, "job_id": job_id,
                                "track_id": track_id, "label": label, "cursor": cursor,
                                "limit": limit}.items() if v is not None}
    return _req("GET", "/events?" + urllib.parse.urlencode(params))


@mcp.tool()
def get_analytics(kind: str, params: dict | None = None) -> dict:
    """Read the platform's computed analytics. kind: summary | heatmap | dwell | occupancy |
    counts | transitions | states. params are the endpoint's query params (since/until epoch seconds,
    group_by, zone_id, ...). Useful to verify your events produce sensible insights.
    All analytics are derived from raw observations — dwell always comes from
    zone_enter/zone_exit pairs, state durations from state_change timestamps."""
    allowed = {"summary", "heatmap", "dwell", "occupancy", "counts", "transitions", "states"}
    if kind not in allowed:
        raise ValueError(f"kind must be one of {sorted(allowed)}")
    qs = urllib.parse.urlencode(params or {})
    return _req("GET", f"/analytics/{kind}" + (f"?{qs}" if qs else ""))


@mcp.tool()
def create_alert_rule(name: str, kind: str, params: dict, webhook_url: str = "",
                      cooldown_s: float = 60) -> dict:
    """Create an alert rule evaluated on every ingested batch. kinds:
      dwell_exceeds {zone_id?, seconds} — a track's platform-derived dwell (enter/exit
        pairing, or an ongoing stay with no exit yet) reaches `seconds` ·
      occupancy_exceeds {zone_id?, count, window_s} ·
      state_alert {label, source_id, min_seconds?} — duration derived from consecutive
        state_change timestamps; also fires while the state is ongoing past the limit ·
      event_match {event_type, zone_id?, attr_key?, attr_value?}.
    Rules only evaluate when events are ingested, so keep a worker streaming.
    Fired alerts appear in the UI and POST to webhook_url (n8n etc.)."""
    return _req("POST", "/alert-rules", {"name": name, "kind": kind, "params": params,
                                         "webhook_url": webhook_url, "cooldown_s": cooldown_s})


@mcp.tool()
def register_insight(title: str, block: str, dataset: str, params: dict | None = None,
                     question: str = "", unit: str = "", limitations: str = "",
                     pinned: bool = False) -> dict:
    """Register a dashboard insight card so your analysis appears in the Insights tab.
    block: metric|line|bar|table|heatmap_map|flow_matrix|state_timeline. dataset: the
    platform analytics feeding it (summary|heatmap|dwell|occupancy|counts|transitions|
    states). params are that analytics endpoint's filters (zone_id, label, group_by,
    source_id, field...). Call list_insight_templates() first to see which combinations
    fit the data, and list_insights() to avoid duplicates. Always state `limitations`
    honestly — it is shown on the card. pinned=True also shows it on Overview."""
    return _req("POST", "/insights", {
        "title": title, "block": block, "dataset": dataset, "params": params or {},
        "question": question, "unit": unit, "limitations": limitations,
        "pinned": pinned, "created_by": "agent",
    })


@mcp.tool()
def list_insights() -> list[dict]:
    """List every registered insight definition (including hidden ones). Check this
    before register_insight to avoid duplicate cards."""
    return _req("GET", "/insights?include_hidden=true")


@mcp.tool()
def list_insight_templates() -> dict:
    """Insight templates assembled from the data actually present (count labels, state
    sources, zones, attribute keys). Each entry has block/dataset/params ready to pass
    to register_insight; unavailable ones carry a `requires` hint."""
    return _req("GET", "/insights/templates")


@mcp.tool()
def update_insight(insight_id: int, patch: dict) -> dict:
    """Patch an insight definition (title, question, params, unit, limitations, pinned,
    sort_order, visibility, status...). Use status='degraded' or 'retired' instead of
    deleting when an analysis stops running."""
    return _req("PUT", f"/insights/{insight_id}", patch)


@mcp.tool()
def delete_insight(insight_id: int) -> dict:
    """Delete an insight definition. Prefer update_insight(status='retired') if the
    card may still be useful as history."""
    return _req("DELETE", f"/insights/{insight_id}")


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
