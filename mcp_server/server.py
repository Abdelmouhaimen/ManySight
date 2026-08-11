"""StoreLens MCP server — an agent-facing adapter for the StoreLens REST API.

Run standalone:            python mcp_server/server.py
Register with Codex CLI:   see codex.config.example.toml at the repo root.

Env:
  STORELENS_URL      base URL of the platform (default http://localhost:8000)
  STORELENS_API_KEY  only if the server enforces one
  STORELENS_CREDENTIAL_ACCESS_KEY  privileged managed-connection resolution key
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

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from mcp_server._transport import build_server, run_server
from server.platform_config import resolve as resolve_platform_config

PLATFORM_ENDPOINTS = resolve_platform_config()
BASE = os.environ.get("STORELENS_URL", PLATFORM_ENDPOINTS["public_url"]).rstrip("/")
REST_BASE = os.environ.get(
    "STORELENS_REST_URL",
    BASE + PLATFORM_ENDPOINTS["paths"].get("rest", "/api/v1"),
).rstrip("/")
API_KEY = os.environ.get("STORELENS_API_KEY", "")
CREDENTIAL_ACCESS_KEY = os.environ.get("STORELENS_CREDENTIAL_ACCESS_KEY", API_KEY)
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

mcp = build_server(
    "storelens",
    instructions=(
        "StoreLens is an agent-operated computer-vision platform for physical spaces. "
        "On the first StoreLens request, always call get_skill('storelens-platform') and "
        "follow that general operating guide before planning or changing the platform. "
        "Then call list_skills() and load the closest task-specific playbook when one applies. "
        "Discover the logical sources, map, zones, jobs, and data instead of assuming "
        "prior conversation or demo state. Camera access is worker-local: StoreLens never opens "
        "or proxies a feed. Managed credentials require explicit privileged resolution through "
        "get_source_connection; ordinary source tools never return them. Use MCP for agent operations; workers "
        "use the REST endpoint returned by get_platform_config(). "
        "\n\nObserve locally, derive centrally: a worker submits ONLY three observation kinds — "
        "detection (an observed entity with spatial evidence), measurement (an observed numeric "
        "value), and state (an observed current categorical value). Call get_observation_contract() "
        "for the exact field-level contract. Workers must NEVER resolve zones, send zone_id/zone, "
        "or calculate zone entry/exit, dwell, occupancy, movement between zones, state changes, or "
        "state durations — StoreLens derives every one of those from raw detection/state rows. "
        "submit_observations() rejects any of those legacy derived kinds with a "
        "legacy_derived_observation error. "
        "\n\nAfter posting observations, verify with get_latest_observations()/query_analytics(), "
        "then create_analysis(subject, measures, filters, grouping) so the result appears on the "
        "dashboard as a saved question, not a chart — changing how it is displayed later never "
        "requires a second analysis. list_analysis_capabilities() shows which subjects/measures/"
        "groupings fit the data actually present. register_insight is deprecated (kept only as a "
        "compatibility adapter to create_analysis) — do not use it for new work. "
        "\n\nA zone polygon is its global map footprint. Use zone views for camera-specific visible "
        "and inset decision polygons, and named projection surfaces for mattresses, tables, or "
        "other elevated planes; never compensate for height by subtracting map Y. Preserve bbox, "
        "keypoints, masks, point meaning, and geometry provenance in submitted observations — "
        "StoreLens uses them for zone assignment and review evidence; it never invents identity."
    ),
)


def _req(method: str, path: str, body: dict | None = None, raw: bool = False,
         privileged: bool = False):
    url = REST_BASE + path
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if privileged:
        if not CREDENTIAL_ACCESS_KEY:
            raise RuntimeError("STORELENS_CREDENTIAL_ACCESS_KEY is required to resolve managed connections")
        headers["X-StoreLens-Credential-Key"] = CREDENTIAL_ACCESS_KEY
    elif API_KEY:
        headers["X-API-Key"] = API_KEY
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=30) as res:
        payload = res.read()
        return payload if raw else json.loads(payload)


@mcp.tool()
def list_sources() -> list[dict]:
    """List logical observation sources, safe connection metadata, capabilities,
    latest worker runtime, observation freshness, placement, and calibration."""
    return _req("GET", "/sources")


@mcp.tool()
def get_platform_config() -> dict:
    """Return the authoritative dashboard, REST, OpenAPI, agent-guide, discovery,
    and MCP endpoints resolved for this StoreLens deployment."""
    return _req("GET", "/platform-config")


@mcp.tool()
def get_source(source_id: int) -> dict:
    """Get one logical source. Normal metadata never contains credentials."""
    return _req("GET", f"/sources/{source_id}")


@mcp.tool()
def get_source_connection(source_id: int) -> dict:
    """Explicitly resolve a source connection for a local worker. Requires
    STORELENS_CREDENTIAL_ACCESS_KEY and may return sensitive credentials. Never log,
    display, or persist the result; pass it directly to worker connection code."""
    return _req("GET", f"/sources/{source_id}/connection", privileged=True)


@mcp.tool()
def create_source(name: str, kind: str = "webcam", connection_mode: str = "agent_local",
                  locator: dict | None = None, capabilities: list[str] | None = None,
                  metadata: dict | None = None,
                  connection_management: str = "external_secret",
                  connection: dict | None = None, credentials: dict | None = None) -> dict:
    """Register a logical source. Use storelens_managed with a structured connection
    and optional credentials, or external_secret with locator.local_secret_ref."""
    return _req("POST", "/sources", {
        "name": name,
        "kind": kind,
        "connection_mode": connection_mode,
        "locator": locator or {},
        "connection_management": connection_management,
        "connection": connection or {},
        "credentials": credentials,
        "capabilities": capabilities or [],
        "metadata": metadata or {},
    })


@mcp.tool()
def update_source(source_id: int, patch: dict) -> dict:
    """Update a source. Omitted credentials are preserved; provide credentials to
    replace them or clear_credentials=true to explicitly remove them."""
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
    create_alert_rule; your worker just posts tracked detections with coordinates
    and never needs to know what the zone means or resolve it itself — StoreLens
    assigns the zone from geometry at ingestion. Confirm the polygon with the
    user before creating it."""
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
    existing observations keep the surface revision used when they were ingested."""
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
    """Register an analysis job before posting observations. Returns the job with its id;
    pass that job_id to schema-v2 observations so the platform can attribute and monitor
    the work."""
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
def get_observation_contract() -> dict:
    """Machine-readable summary of the current worker contract: the three
    observation kinds (detection|measurement|state), required/optional fields
    per kind, and what is forbidden (zone_id/zone, and the legacy derived kinds
    zone_enter/zone_exit/zone_dwell/state_change/count). Call this before
    writing a new worker."""
    return _req("GET", "/observations/contract")


@mcp.tool()
def submit_observations(observations: list[dict], job_id: int | None = None) -> dict:
    """Post a batch of raw observations (max 5000) — THE primary ingestion tool.
    Each observation is exactly one kind: detection (an observed entity with
    spatial evidence), measurement (an observed numeric value), or state (an
    observed current categorical value). Common fields: schema_version (2),
    observation_id (a worker-generated idempotency key — retries are safe),
    kind, timestamp (epoch seconds or ISO-8601), source_id, and optionally
    worker_id, confidence, label, attributes, entity_id (opaque per-track id;
    never a verified human identity), identity_scope (worker_run|source|
    workspace, default worker_run), identity_model_version.
    Kind-specific: detection adds entity_type and geometry {point_px:[x,y],
    bbox_px:[x0,y0,x1,y1], keypoints_px:{name:[x,y]}, mask}; measurement adds
    name, value, value_kind (gauge|delta|cumulative, default gauge), unit;
    state adds name, label (the observed value), info.
    Never send zone_id/zone (StoreLens assigns zones from geometry) or the
    legacy kinds zone_enter/zone_exit/zone_dwell/state_change/count — those are
    rejected per-item with error 'legacy_derived_observation'. A worker sends
    what a model directly observed; StoreLens derives visits, dwell, occupancy,
    movement, state transitions/durations, and every analysis from these rows.
    Returns {accepted, duplicates, rejected: [{index, observation_id, error,
    message}], alerts} — duplicates (same observation_id already stored) are
    not errors, they're a safe no-op retry."""
    return _req("POST", "/observations/batch", {"job_id": job_id, "observations": observations})


@mcp.tool()
def list_observations(since: float | None = None, until: float | None = None,
                      kind: str | None = None, source_id: int | None = None,
                      entity_id: str | None = None, name: str | None = None,
                      label: str | None = None, zone_id: int | None = None,
                      cursor: str | None = None, limit: int = 100) -> dict:
    """Query stored observations (current + legacy kinds), newest first — use it
    to sanity-check what your worker posted, including the zone/projection it was
    assigned and the geometry revisions in effect at ingestion. Page with the
    returned next_cursor; `total` counts all matching rows."""
    params = {k: v for k, v in {"since": since, "until": until, "kind": kind, "source_id": source_id,
                                "entity_id": entity_id, "name": name, "label": label, "zone_id": zone_id,
                                "cursor": cursor, "limit": limit}.items() if v is not None}
    return _req("GET", "/observations?" + urllib.parse.urlencode(params))


@mcp.tool()
def get_latest_observations(kind: str | None = None, source_id: int | None = None,
                            zone_id: int | None = None, name: str | None = None) -> dict:
    """Current-value read models, derived live from raw observations (never a
    separate stored copy): detection -> active/latest entities with staleness;
    measurement -> latest sample per (source, name, label, entity); state ->
    current label and duration per (source, name, entity), marked stale once a
    worker stops reporting. Omit `kind` to get all three."""
    params = {k: v for k, v in {"kind": kind, "source_id": source_id, "zone_id": zone_id,
                                "name": name}.items() if v is not None}
    return _req("GET", "/observations/latest" + ("?" + urllib.parse.urlencode(params) if params else ""))


@mcp.tool()
def submit_events(events: list[dict], job_id: int | None = None) -> dict:
    """LEGACY. Prefer submit_observations for all new work. Post a batch of raw
    events (max 5000) using the older per-event-type contract (event_type:
    detection|zone_enter|zone_exit|transition|state_change|count|custom).
    Still accepted for backward compatibility, but zone_enter/zone_exit/
    zone_dwell/state_change/count are worker-calculated derived events that the
    current contract does not want new workers to send — see
    get_observation_contract() for what to send instead."""
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
    """LEGACY. Prefer query_analytics for new work. Read the platform's per-kind
    analytics. kind: summary | heatmap | dwell | occupancy | counts | transitions |
    states. params are the endpoint's query params (since/until epoch seconds,
    group_by, zone_id, ...). All analytics are derived from raw observations —
    dwell/occupancy/transitions now come from tracked detections (or legacy
    zone_enter/zone_exit pairs), state durations from state/state_change samples."""
    allowed = {"summary", "heatmap", "dwell", "occupancy", "counts", "transitions", "states"}
    if kind not in allowed:
        raise ValueError(f"kind must be one of {sorted(allowed)}")
    qs = urllib.parse.urlencode(params or {})
    return _req("GET", f"/analytics/{kind}" + (f"?{qs}" if qs else ""))


@mcp.tool()
def list_analysis_capabilities() -> dict:
    """What query_analytics()/create_analysis() can build a question from right
    now: subjects (detection|measurement|state), their valid measures, grouping
    options, split dimensions, and the labels/sources/zones/measurement-names/
    state-names/attribute-keys actually present in the data. Call this before
    query_analytics/create_analysis so you never guess an invalid combination."""
    return _req("GET", "/analytics/capabilities")


@mcp.tool()
def query_analytics(subject: str, measures: list[str], filters: dict | None = None,
                    grouping: dict | None = None, range: dict | None = None,
                    comparison: dict | None = None) -> dict:
    """Answer one analytical question directly, without saving it. subject:
    detection|measurement|state. measures: e.g. ["active_entities"],
    ["visits","average_dwell","total_dwell"], ["latest","rate"],
    ["current","duration"] — see list_analysis_capabilities() for the valid set
    per subject. filters: {source_ids, zone_ids, labels, entity_types,
    entity_ids, attributes:{key:value}, measurement_names, state_names,
    state_labels}. grouping: {primary: null|"time"|"zone", bucket: "5m",
    split_by: ["label", "attribute:gender", ...]}. range: {since, until} (epoch
    seconds or ISO-8601; defaults to the last 24h). comparison:
    {mode: "previous_period"} to also return the prior equal-length window.
    Returns {shape, dimensions, measures, rows, metadata} — shape tells the
    caller how to read rows (scalar|timeseries|categorical|heatmap), never
    which chart to draw; that is a frontend rendering choice, not part of the
    question's identity."""
    body = {"subject": subject, "measures": measures, "filters": filters or {},
            "grouping": grouping or {}, "range": range or {}, "comparison": comparison or {}}
    return _req("POST", "/analytics/query", body)


@mcp.tool()
def create_analysis(name: str, subject: str, measures: list[str], filters: dict | None = None,
                    grouping: dict | None = None, question: str = "", default_range: dict | None = None,
                    comparison: dict | None = None, presentation: str = "", pinned: bool = False) -> dict:
    """Save a data question so it appears on the dashboard's Analytics page (and
    Dashboard, if pinned). This is a QUESTION, not a chart — subject/measures/
    filters/grouping, exactly like query_analytics(). `presentation` is only an
    optional renderer hint (e.g. "heatmap_map", "flow_matrix", "state_timeline");
    changing how a saved analysis is displayed later never needs a new record —
    patch presentation on the same analysis with update_analysis instead of
    creating a second one for the same question. Call list_analysis_capabilities()
    first, and list_analyses() to avoid registering a duplicate. `question` is
    shown to the user (e.g. "How long do customers stay near checkout?")."""
    return _req("POST", "/analyses", {
        "name": name, "subject": subject, "measures": measures, "filters": filters or {},
        "grouping": grouping or {}, "question": question, "default_range": default_range or {},
        "comparison": comparison or {}, "presentation": presentation, "pinned": pinned,
        "created_by": "agent",
    })


@mcp.tool()
def list_analyses() -> list[dict]:
    """List every saved analysis (including hidden ones) — check this before
    create_analysis to avoid a duplicate; identical (subject, measures, filters,
    grouping) is flagged via `duplicate_of` on creation regardless."""
    return _req("GET", "/analyses?include_hidden=true")


@mcp.tool()
def update_analysis(analysis_id: int, patch: dict) -> dict:
    """Patch a saved analysis (name, question, measures, filters, grouping,
    presentation, pinned, sort_order, visibility, status...). Use
    status='degraded'/'retired' instead of deleting when its worker stops."""
    return _req("PATCH", f"/analyses/{analysis_id}", patch)


@mcp.tool()
def delete_analysis(analysis_id: int) -> dict:
    """Delete a saved analysis. Prefer update_analysis(status='retired') if it
    may still be useful as history."""
    return _req("DELETE", f"/analyses/{analysis_id}")


@mcp.tool()
def create_alert_rule(name: str, kind: str, params: dict | None = None, analysis: dict | None = None,
                      condition: dict | None = None, webhook_url: str = "", cooldown_s: float = 60) -> dict:
    """Create an alert rule. kinds:
      dwell_exceeds {zone_id?, seconds} (params) — a track's platform-derived dwell
        (tracked detections, or legacy enter/exit pairing) reaches `seconds` ·
      occupancy_exceeds {zone_id?, count, window_s} (params) ·
      state_alert {label, source_id, name?, entity_id?, min_seconds?} (params) — duration
        derived from consecutive state samples (repeated identical samples never reset it) ·
      event_match {event_type, zone_id?, attr_key?, attr_value?} (params) ·
      analysis_condition (analysis + condition) — the general case: analysis is
        {subject, measures, filters} exactly like query_analytics(); condition is
        {operator: ">"|">="|"<"|"<="|"=="|"!=", value, for_seconds?, window_s?}. Fires once
        the measure has satisfied the condition continuously for `for_seconds`
        (default 0 = immediately), e.g. a measurement staying over a threshold or
        active_entities staying above a crowd limit.
    Ongoing/time-based conditions (dwell_exceeds, occupancy_exceeds, state_alert with
    min_seconds, and every analysis_condition) are evaluated on a periodic timer
    independent of ingestion (every ~15s), so a quiet zone or a stale source still
    gets caught — they do not require another observation to arrive.
    Fired alerts appear in the UI and POST to webhook_url (n8n etc.)."""
    return _req("POST", "/alert-rules", {"name": name, "kind": kind, "params": params or {},
                                         "analysis": analysis, "condition": condition,
                                         "webhook_url": webhook_url, "cooldown_s": cooldown_s})


_INSIGHT_DATASET_TO_SUBJECT = {
    "summary": ("detection", ["distinct_entities"]), "heatmap": ("detection", ["density"]),
    "dwell": ("detection", ["visits", "average_dwell", "total_dwell"]),
    "occupancy": ("detection", ["active_entities"]), "counts": ("measurement", ["latest"]),
    "transitions": ("detection", ["transition_count"]), "states": ("state", ["duration"]),
}


@mcp.tool()
def register_insight(title: str, block: str, dataset: str, params: dict | None = None,
                     question: str = "", unit: str = "", limitations: str = "",
                     pinned: bool = False) -> dict:
    """DEPRECATED — compatibility adapter only. Use create_analysis for new work;
    the block+dataset+params model this tool used is retired. This translates
    your call into the closest create_analysis() equivalent on a best-effort
    basis (see server/db.py's legacy insight mapping for the exact rules) and
    saves it there, so old agent prompts don't hard-fail, but the mapping is
    approximate — always prefer calling create_analysis directly."""
    subject, measures = _INSIGHT_DATASET_TO_SUBJECT.get(dataset, ("detection", ["observations"]))
    params = params or {}
    filters = {"zone_ids": [params["zone_id"]]} if params.get("zone_id") is not None else {}
    if params.get("label"):
        key = "measurement_names" if subject == "measurement" else "labels"
        filters[key] = [params["label"]]
    grouping = {"split_by": [params["group_by"]]} if params.get("group_by") else {}
    return _req("POST", "/analyses", {
        "name": title, "subject": subject, "measures": measures, "filters": filters,
        "grouping": grouping, "question": question, "presentation": block, "pinned": pinned,
        "created_by": "agent",
    })


@mcp.tool()
def list_insights() -> list[dict]:
    """LEGACY, READ-ONLY — historical insight_definitions rows only; there is no
    create/update path anymore (the block+dataset+params model is retired). Use
    list_analyses() for current work; every insight has already been
    best-effort migrated to an analysis (see analyses[].migrated_from_insight_id)."""
    return _req("GET", "/insights?include_hidden=true")


@mcp.tool()
def delete_insight(insight_id: int) -> dict:
    """LEGACY. Delete a historical insight_definitions row (cleanup only). Prefer
    delete_analysis/update_analysis(status='retired') on its migrated analysis."""
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
    run_server(
        mcp, transport, host=MCP_HOST, port=MCP_PORT,
        dns_rebinding_protection=MCP_DNS_REBINDING_PROTECTION,
        allowed_hosts=MCP_ALLOWED_HOSTS, allowed_origins=MCP_ALLOWED_ORIGINS,
    )
