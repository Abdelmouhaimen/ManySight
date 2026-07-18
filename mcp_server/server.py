"""StoreLens MCP server — the bridge that lets Codex (or any MCP client) operate the platform.

Run standalone:            python mcp_server/server.py
Register with Codex CLI:   see codex.config.example.toml at the repo root.

Env:
  STORELENS_URL      base URL of the platform (default http://localhost:8000)
  STORELENS_API_KEY  only if the server enforces one
  STORELENS_SKILLS   path to the skills/ folder (default: sibling of this file's parent)
  STORELENS_MCP_TRANSPORT  stdio (default) | streamable-http
  STORELENS_MCP_HOST / STORELENS_MCP_PORT  remote transport bind settings
  STORELENS_MCP_ALLOWED_HOSTS / STORELENS_MCP_ALLOWED_ORIGINS  comma-separated
"""
import json
import os
import sys
import urllib.parse
import urllib.request

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.server import TransportSecuritySettings

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from server.platform_config import resolve as resolve_platform_config

PLATFORM_ENDPOINTS = resolve_platform_config()
BASE = os.environ.get("STORELENS_URL", PLATFORM_ENDPOINTS["public_url"]).rstrip("/")
REST_BASE = os.environ.get(
    "STORELENS_REST_URL",
    BASE + PLATFORM_ENDPOINTS["paths"].get("rest", "/api/v1"),
).rstrip("/")
API_KEY = os.environ.get("STORELENS_API_KEY", "")
SKILLS_DIR = os.environ.get(
    "STORELENS_SKILLS",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "skills"),
)
MCP_HOST = os.environ.get("STORELENS_MCP_HOST", "127.0.0.1")
MCP_PORT = int(os.environ.get("STORELENS_MCP_PORT", "8001"))
MCP_DNS_REBINDING_PROTECTION = os.environ.get(
    "STORELENS_MCP_DNS_REBINDING_PROTECTION", "true"
).lower() in {"1", "true", "yes"}
MCP_ALLOWED_HOSTS = [
    value.strip() for value in os.environ.get(
        "STORELENS_MCP_ALLOWED_HOSTS", "127.0.0.1:*,localhost:*,[::1]:*"
    ).split(",") if value.strip()
]
MCP_ALLOWED_ORIGINS = [
    value.strip() for value in os.environ.get(
        "STORELENS_MCP_ALLOWED_ORIGINS", "http://127.0.0.1:*,http://localhost:*"
    ).split(",") if value.strip()
]

mcp = FastMCP(
    "storelens",
    host=MCP_HOST,
    port=MCP_PORT,
    stateless_http=True,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=MCP_DNS_REBINDING_PROTECTION,
        allowed_hosts=MCP_ALLOWED_HOSTS,
        allowed_origins=MCP_ALLOWED_ORIGINS,
    ),
    instructions=(
        "StoreLens is an agent-operated computer-vision platform for physical spaces. "
        "On the first StoreLens request, always call get_skill('storelens-platform') and "
        "follow that general operating guide before planning or changing the platform. "
        "Then call list_skills() and load the closest task-specific playbook when one applies. "
        "Discover the logical sources, map, zones, jobs, and data instead of assuming "
        "prior conversation or demo state. Camera access is agent-local: StoreLens never opens "
        "a feed and never returns camera credentials. Use MCP for agent operations; workers "
        "use the REST endpoint returned by get_platform_config(). "
        "Post raw observations only (what the model saw), never computed aggregates — the "
        "platform derives dwell, durations, and every insight. After posting events, register "
        "the resulting view with register_insight so it appears in the Insights catalogue. "
        "A zone polygon is its global map footprint. Use zone views for camera-specific visible "
        "and inset decision polygons, and named projection surfaces for mattresses, tables, or "
        "other elevated planes; never compensate for height by subtracting map Y. Preserve bbox, "
        "keypoints, masks, point meaning, and geometry provenance in submitted observations."
    ),
)


def _req(method: str, path: str, body: dict | None = None, raw: bool = False):
    url = REST_BASE + path
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
    """List logical observation sources, non-secret local locator hints, capabilities,
    latest worker runtime, observation freshness, placement, and calibration."""
    return _req("GET", "/sources")


@mcp.tool()
def get_platform_config() -> dict:
    """Return the authoritative dashboard, REST, OpenAPI, agent-guide, discovery,
    and MCP endpoints resolved for this StoreLens deployment."""
    return _req("GET", "/platform-config")


@mcp.tool()
def get_source(source_id: int) -> dict:
    """Get one logical source. It contains no camera URL or credential. Resolve webcam
    indices or local_secret_ref values on the machine where the worker will run."""
    return _req("GET", f"/sources/{source_id}")


@mcp.tool()
def create_source(name: str, kind: str = "webcam", connection_mode: str = "agent_local",
                  locator: dict | None = None, capabilities: list[str] | None = None,
                  metadata: dict | None = None) -> dict:
    """Register a logical source before creating a job. `locator` is a non-secret hint such
    as {"device_index": 0} or {"local_secret_ref": "warehouse-entrance"}. Never send camera
    URLs, usernames, passwords, API keys, or tokens to StoreLens."""
    return _req("POST", "/sources", {
        "name": name,
        "kind": kind,
        "connection_mode": connection_mode,
        "locator": locator or {},
        "capabilities": capabilities or [],
        "metadata": metadata or {},
    })


@mcp.tool()
def update_source(source_id: int, patch: dict) -> dict:
    """Update a logical source's name, kind, connection mode, non-secret locator,
    capabilities, or metadata. Camera credentials are rejected."""
    return _req("PUT", f"/sources/{source_id}", patch)


@mcp.tool()
def delete_source(source_id: int) -> dict:
    """Delete a logical source and its geometry. Historical observations remain queryable."""
    return _req("DELETE", f"/sources/{source_id}")


@mcp.tool()
def get_store_map() -> dict:
    """The store floor plan: name, dimensions in meters, wall polylines, text labels, all zones
    (named polygons with a semantic type: checkout/entrance/fridge/aisle/...), and every placed
    camera with its position, rotation, FOV and calibration state."""
    store = _req("GET", "/store")
    store["zones"] = _req("GET", "/zones")
    store["projection_surfaces"] = _req("GET", "/projection-surfaces")
    store["zone_views"] = _req("GET", "/zone-views")
    store["cameras"] = [
        {k: s[k] for k in ("id", "name", "kind", "observation_status", "placement", "calibrated")}
        for s in _req("GET", "/sources")
    ]
    return store


@mcp.tool()
def list_zones() -> list[dict]:
    """All named zones as polygons in map meters. Zone ids are what events reference."""
    return _req("GET", "/zones")


@mcp.tool()
def update_zone(zone_id: int, patch: dict) -> dict:
    """Update a global map zone. Its polygon is a physical footprint in metres;
    camera-specific visible/inset polygons belong in a zone view."""
    return _req("PUT", f"/zones/{zone_id}", patch)


@mcp.tool()
def create_zone(name: str, ztype: str = "area",
                polygon_map: list[dict] | None = None,
                polygon_px: list[dict] | None = None,
                source_id: int | None = None) -> dict:
    """Create a named global physical footprint. When starting from a local frame, use
    polygon_px only for points on the calibrated floor plane; then create a zone view
    for the camera-specific visible boundary and inset detection ROI.
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
def project_points(source_id: int, points: list[dict], surface_id: int | None = None) -> dict:
    """Project camera pixels to map metres on the floor (surface_id omitted) or a named
    elevated plane. Never compensate for physical height by subtracting map Y."""
    return _req("POST", f"/sources/{source_id}/project",
                {"points": points, "surface_id": surface_id})


@mcp.tool()
def unproject_points(source_id: int, points: list[dict], surface_id: int | None = None) -> dict:
    """Project map-metre points into a camera frame on the selected plane."""
    return _req("POST", f"/sources/{source_id}/unproject",
                {"points": points, "surface_id": surface_id})


@mcp.tool()
def list_projection_surfaces(source_id: int | None = None) -> list[dict]:
    """List named source-specific planes such as mattress, table, shelf, or conveyor."""
    suffix = "" if source_id is None else "?" + urllib.parse.urlencode({"source_id": source_id})
    return _req("GET", "/projection-surfaces" + suffix)


@mcp.tool()
def create_projection_surface(source_id: int, name: str, points: list[dict],
                              kind: str = "custom", height_m: float | None = None,
                              frame_w: int | None = None,
                              frame_h: int | None = None) -> dict:
    """Create a plane homography from at least four {px,map} pairs. Use this for an
    elevated planar target instead of pixel offsets or height subtraction."""
    return _req("POST", "/projection-surfaces", {
        "source_id": source_id, "name": name, "kind": kind, "height_m": height_m,
        "points": points, "frame_w": frame_w, "frame_h": frame_h,
    })


@mcp.tool()
def update_projection_surface(surface_id: int, patch: dict) -> dict:
    """Update a named plane and recompute its homography. Its revision increments;
    existing events keep the surface revision used when they were ingested."""
    return _req("PUT", f"/projection-surfaces/{surface_id}", patch)


@mcp.tool()
def delete_projection_surface(surface_id: int) -> dict:
    """Delete an unused named plane. Remove or repoint dependent zone views first."""
    return _req("DELETE", f"/projection-surfaces/{surface_id}")


@mcp.tool()
def list_zone_views(source_id: int | None = None, zone_id: int | None = None) -> list[dict]:
    """List per-camera zone geometry: visible outer polygon, inset detection ROI,
    projection surface, and membership rule."""
    params = {k: v for k, v in {"source_id": source_id, "zone_id": zone_id}.items()
              if v is not None}
    return _req("GET", "/zone-views" + ("?" + urllib.parse.urlencode(params) if params else ""))


@mcp.tool()
def create_zone_view(zone_id: int, source_id: int, outer_polygon_px: list[dict],
                     detection_polygon_px: list[dict] | None = None,
                     projection_surface_id: int | None = None,
                     membership_rule: str = "point", threshold: float = 0.5,
                     min_keypoints: int = 1) -> dict:
    """Create a camera view of a global zone after user confirmation. Membership rules:
    point, bbox_overlap, or keypoints_inside."""
    return _req("POST", "/zone-views", {
        "zone_id": zone_id, "source_id": source_id,
        "outer_polygon_px": outer_polygon_px,
        "detection_polygon_px": detection_polygon_px,
        "projection_surface_id": projection_surface_id,
        "membership_rule": membership_rule, "threshold": threshold,
        "min_keypoints": min_keypoints,
    })


@mcp.tool()
def update_zone_view(view_id: int, patch: dict) -> dict:
    """Update a camera ROI, decision rule, or plane. The view revision increments."""
    return _req("PUT", f"/zone-views/{view_id}", patch)


@mcp.tool()
def delete_zone_view(view_id: int) -> dict:
    """Delete one camera-specific view without deleting the global map zone."""
    return _req("DELETE", f"/zone-views/{view_id}")


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
def list_jobs() -> list[dict]:
    """List analysis registrations and their latest heartbeat-backed worker instance."""
    return _req("GET", "/jobs")


@mcp.tool()
def list_workers(job_id: int | None = None) -> list[dict]:
    """List concrete worker instances. effective_status becomes stale without heartbeats;
    job status alone is not proof that a process is alive."""
    suffix = "" if job_id is None else "?" + urllib.parse.urlencode({"job_id": job_id})
    return _req("GET", "/workers" + suffix)


@mcp.tool()
def register_worker(job_id: int, name: str = "", version: str = "",
                    worker_id: str | None = None, config: dict | None = None) -> dict:
    """Register a concrete worker instance when launching it. Persistent workers should
    normally call this REST endpoint through the SDK themselves, then heartbeat every
    5–15 seconds. Do not register a worker that was not actually started."""
    return _req("POST", "/workers", {
        "job_id": job_id, "name": name, "version": version,
        "worker_id": worker_id, "config": config or {},
    })


@mcp.tool()
def heartbeat_worker(worker_id: int, status: str = "running",
                     metrics: dict | None = None, last_error: str = "") -> dict:
    """Send one lifecycle heartbeat. The response includes should_stop and
    restart_requested. A worker must obey those flags and exit cleanly; a supervisor
    is responsible for relaunch after restart."""
    return _req("POST", f"/workers/{worker_id}/heartbeat", {
        "status": status, "metrics": metrics or {}, "last_error": last_error,
    })


@mcp.tool()
def request_worker_state(worker_id: int, desired_state: str) -> dict:
    """Request running, stopped, or restart. The worker/supervisor must obey the command.
    `restart` cannot create a process if no supervisor exists; StoreLens never executes
    arbitrary user scripts inside the web process."""
    return _req("PUT", f"/workers/{worker_id}/desired-state",
                {"desired_state": desired_state})


@mcp.tool()
def submit_events(events: list[dict], job_id: int | None = None) -> dict:
    """Post a batch of raw observations (max 5000). Event fields:
      ts (epoch seconds, optional), source_id, event_type (detection|zone_enter|zone_exit|
      transition|state_change|count|custom), track_id, point_px {x,y} OR point_map {x,y}
      OR bbox [x,y,w,h], keypoints, compressed mask evidence, point_kind,
      projection_surface_id, zone_view_id, zone_id or zone (name), value (a per-frame
      count sample only), label, and attributes. Geometry evidence and revision
      provenance are persisted; use a named surface for elevated planar targets.
    Contract: post what the model SAW, never computed aggregates. The platform derives
    dwell from zone_enter/zone_exit pairs and state durations from state_change
    timestamps. `zone_dwell` is deprecated: still stored, but its value is ignored by
    analytics and alerts. The platform auto-projects point_px to map meters (if the
    source is calibrated) and can assign zones through map geometry or a source-specific
    zone view (point, bbox overlap, or keypoints-inside rule).
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
    group_by, zone_id, ...). Occupancy and heatmap accept the top-level detection `label`;
    occupancy also accepts group_by="label" to return per-class series. Counts accepts
    the top-level count-event `label`. Useful to verify your events produce sensible insights.
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
    source_id, field...). For labelled detections, use occupancy with label="class" to
    filter one class or group_by="label" to compare class lines. Call
    list_insight_templates() first to see which combinations
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
    """List the operating guide and analysis playbooks shipped with StoreLens. Load
    `storelens-platform` first, then the closest task-specific skill."""
    out = []
    if os.path.isdir(SKILLS_DIR):
        for entry in sorted(os.listdir(SKILLS_DIR)):
            path = os.path.join(SKILLS_DIR, entry, "SKILL.md")
            if os.path.isfile(path):
                with open(path, encoding="utf-8") as f:
                    content = f.read()
                title = next(
                    (line[2:].strip() for line in content.splitlines() if line.startswith("# ")),
                    entry,
                )
                out.append({"name": entry, "title": title})
    return out


@mcp.tool()
def get_skill(name: str) -> str:
    """Return a complete StoreLens operating guide or task playbook. Load
    `storelens-platform` first on every new StoreLens task."""
    path = os.path.join(SKILLS_DIR, name, "SKILL.md")
    if not os.path.isfile(path):
        raise ValueError(f"unknown skill '{name}' — call list_skills() first")
    with open(path, encoding="utf-8") as f:
        content = f.read()
    endpoints = get_platform_config()
    runtime = (
        "## Runtime endpoints (authoritative for this connection)\n\n"
        f"- Dashboard: `{endpoints['dashboard_url']}`\n"
        f"- REST base: `{endpoints['rest_url']}`\n"
        f"- OpenAPI: `{endpoints['openapi_url']}`\n"
        f"- Interactive docs: `{endpoints['docs_url']}`\n"
        f"- Remote MCP: `{endpoints['mcp_url']}`\n"
        f"- Agent guide: `{endpoints['agent_guide_url']}`\n\n"
        "Use these resolved values instead of hard-coded hosts.\n\n"
    )
    return runtime + content


if __name__ == "__main__":
    transport = os.environ.get("STORELENS_MCP_TRANSPORT", "stdio")
    if transport not in {"stdio", "streamable-http"}:
        raise SystemExit("STORELENS_MCP_TRANSPORT must be stdio or streamable-http")
    mcp.run(transport=transport)
