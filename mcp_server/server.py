"""ManySight MCP server — the curated semantic surface coding agents operate.

Three interfaces, three jobs:

  REST / SDK  complete low-level platform interface (see /openapi.json)
  MCP         a small semantic interface shaped for an agent's context window
  skills      the authoritative workflow knowledge behind these tools

This module advertises a deliberately small public tool set. The low-level
handlers are still implemented below as plain module functions — importable,
testable, and re-registerable with MANYSIGHT_MCP_LEGACY_TOOLS=1 — they are just
not part of the normal advertised surface, because an agent that has to choose
between sixty tools rediscovers the architecture by trial and error instead of
following it.

Run standalone:            python mcp_server/server.py
Register with Codex CLI:   see codex.config.example.toml at the repo root.

Env:
  MANYSIGHT_URL      base URL of the platform (default http://localhost:8000)
  MANYSIGHT_API_KEY  only if the server enforces one
  MANYSIGHT_SKILLS   path to the skills/ folder (default: sibling of this file's parent)
  MANYSIGHT_MCP_TRANSPORT  stdio (default) | streamable-http
  MANYSIGHT_MCP_HOST / MANYSIGHT_MCP_PORT  remote transport bind settings
  MANYSIGHT_MCP_ALLOWED_HOSTS / MANYSIGHT_MCP_ALLOWED_ORIGINS  comma-separated
  MANYSIGHT_MCP_LEGACY_TOOLS  1 to also advertise the deprecated low-level tools
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
BASE = os.environ.get("MANYSIGHT_URL", PLATFORM_ENDPOINTS["public_url"]).rstrip("/")
REST_BASE = os.environ.get(
    "MANYSIGHT_REST_URL",
    BASE + PLATFORM_ENDPOINTS["paths"].get("rest", "/api/v1"),
).rstrip("/")
API_KEY = os.environ.get("MANYSIGHT_API_KEY", "")
SKILLS_DIR = os.environ.get(
    "MANYSIGHT_SKILLS",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "skills"),
)
MCP_HOST = os.environ.get("MANYSIGHT_MCP_HOST", "127.0.0.1")
MCP_PORT = int(os.environ.get("MANYSIGHT_MCP_PORT", "8001"))
LEGACY_TOOL_MODE = os.environ.get("MANYSIGHT_MCP_LEGACY_TOOLS", "").lower() in {"1", "true", "yes"}
MCP_DNS_REBINDING_PROTECTION = os.environ.get(
    "MANYSIGHT_MCP_DNS_REBINDING_PROTECTION", "true"
).lower() in {"1", "true", "yes"}
MCP_ALLOWED_HOSTS = [
    value.strip() for value in os.environ.get(
        "MANYSIGHT_MCP_ALLOWED_HOSTS", "127.0.0.1:*,localhost:*,[::1]:*"
    ).split(",") if value.strip()
]
MCP_ALLOWED_ORIGINS = [
    value.strip() for value in os.environ.get(
        "MANYSIGHT_MCP_ALLOWED_ORIGINS", "http://127.0.0.1:*,http://localhost:*"
    ).split(",") if value.strip()
]

COMPARISON_OPERATORS = (">", ">=", "<", "<=", "==", "!=")

mcp = build_server(
    "manysight",
    instructions=(
        "ManySight turns observations produced by LOCAL workers into spatial and temporal state "
        "for a physical space: geometry, zones, cross-camera fusion, deterministic queries, "
        "dashboards, and alerts.\n"
        "\n"
        "START HERE. Call inspect_workspace() first on almost every task — it returns the "
        "sources, calibration, zones, perception freshness, multiview groups, saved queries, "
        "dashboards, alert rules, and readiness in one response. Then call list_workflows() and "
        "get_workflow(name) for the job you were asked to do, and get_skill(name) for the full "
        "playbook. Do not rediscover the architecture by trying tools.\n"
        "\n"
        "OBSERVE LOCALLY, DERIVE CENTRALLY. Workers submit only raw perception evidence: "
        "detection (an observed entity with pixel evidence), measurement (an observed number), "
        "state (an observed categorical value). ManySight derives projection, canonical zone "
        "assignment, visits, dwell, occupancy, transitions, state durations, multiview fusion, "
        "queries, and alerts. Workers must never submit zone_id/zone, zone_enter, zone_exit, "
        "zone_dwell, state_change, count, occupancy, visits, transitions, or fused identity.\n"
        "\n"
        "ONE ATOMIC SAMPLE PER PROCESSED FRAME. A person-detection worker posts one "
        "DetectionSample per processed frame to POST /api/v1/detection-samples. detections=[] is "
        "an explicit KNOWN ZERO and must be submitted; never fake a detection for an empty frame. "
        "No fresh complete sample means UNKNOWN or STALE, never zero. Call get_worker_recipe() "
        "for the current contract — never infer it from an example, demo, or older worker script "
        "you find in a repository; those may predate the current API.\n"
        "\n"
        "TRACKING RATE AND HARDWARE. A tracking worker processes at least 15 frames/sec per "
        "camera when the source supplies that and the machine sustains it; prefer 30 or "
        "source-native. Never quietly configure person tracking at 1-5 FPS on capable hardware. "
        "Local processing FPS and central submission Hz are different rates — submission is "
        "normally lower, and gating submission is not a reason to slow the tracker. Before "
        "starting a heavy worker, inspect the local machine yourself: existing virtualenv/conda "
        "environments, nvidia-smi, and torch.cuda inside the interpreter that will run the "
        "worker (manysight.probe_perception_runtime() does all of it). Prefer GPU when it is "
        "there; CPU is a valid fallback and never makes a camera unusable. Do not ask the user "
        "whether they have CUDA or which environment to use when you can find out. After "
        "starting, verify the achieved rate, not just that samples arrived.\n"
        "\n"
        "IDENTITY. entity_id is an opaque source-local tracker ID, not a person. Fused multiview "
        "IDs are anonymous physical-track estimates from geometry, time, and topology — not "
        "identity, not appearance ReID. Never join tracker IDs across cameras yourself; "
        "cross-camera occupancy uses fused entities, never a count of raw local track IDs.\n"
        "\n"
        "GEOMETRY. A ZoneView is one camera's pixel polygon; the canonical Zone is the single "
        "physical footprint in map metres. One physical region is ONE canonical zone, never one "
        "per camera. When a named region has no geometry yet, inspect the calibrated cameras "
        "first (plan_frame_capture) instead of asking the user for coordinates, then "
        "preview_zone, get approval, and commit_zone.\n"
        "\n"
        "ANALYTICS. The saved query computes; a dashboard only presents. Threshold words are "
        "exact: 'more than 2' is > 2 and 'at least 2' is >= 2 — ManySight never converts one into "
        "the other. Quality known/partial/unknown are different: a stale camera does not mean "
        "zero. Agents never receive raw SQL and never generate dashboard code.\n"
        "\n"
        "BOUNDARIES. ManySight never opens or proxies a camera feed, runs a model, or executes "
        "your scripts; camera access and inference are yours, locally. Credentials come only from "
        "get_source_connection inside an authorized worker and never appear in observations, "
        "queries, dashboards, logs, or job metadata. Space and observation reinitialization are "
        "destructive and require an explicit user request."
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


def _qs(params: dict) -> str:
    clean = {k: v for k, v in params.items() if v is not None}
    return "?" + urllib.parse.urlencode(clean) if clean else ""


def _ids(source_ids: list[int] | None) -> str | None:
    return ",".join(str(int(value)) for value in source_ids) if source_ids else None


# ===========================================================================
# CURATED PUBLIC SURFACE
# ===========================================================================

# --- context and workflows -------------------------------------------------

@mcp.tool()
def inspect_workspace(entity_type: str = "person") -> dict:
    """FIRST CALL for almost every ManySight task. One read-only snapshot of the
    whole workspace: space and current space revision, map readiness, every source
    with configuration/placement/calibration/freshness, canonical zones and their
    per-camera view coverage, perception freshness for `entity_type`, multiview
    groups with per-source calibration and quality, saved queries, dashboards,
    alert rules, query capabilities, plus a readiness summary and next steps.

    Use it before planning, before creating anything, and to verify afterwards.
    Do not reconstruct this from a dozen low-level reads, and do not assume state
    from an earlier conversation or a demo. Never contains credentials."""
    return _req("GET", "/agent/workspace" + _qs({"entity_type": entity_type}))


@mcp.tool()
def list_workflows() -> dict:
    """The index of ManySight jobs an agent can be asked to do (define a zone from
    cameras, create a zone occupancy alert, run person tracking, configure
    multiview, generate a dashboard, onboard or inspect a camera).

    Use it when you know the user's goal but not the ManySight path to it. Cheap;
    returns names, when-to-use, and the skills behind each. Then call
    get_workflow(name)."""
    return _req("GET", "/agent/workflows")


@mcp.tool()
def get_workflow(name: str) -> dict:
    """One workflow's prerequisites, ordered sequence, non-negotiable invariants,
    the MCP tools that implement it, and what 'done' means.

    Use it right after list_workflows() and before acting. It is the routing
    record, not the full playbook — load the named skill with get_skill() when you
    need the detail behind a step."""
    return _req("GET", f"/agent/workflows/{urllib.parse.quote(name)}")


@mcp.tool()
def get_skill(name: str) -> str:
    """Return one complete ManySight playbook: manysight-core (load first),
    sources-and-cameras, geometry-and-zones, perception-workers, multiview-fusion,
    queries-dashboards-alerts, guided-demo.

    Use it when a workflow step needs the reasoning behind it, or on any ManySight
    task where you are unsure of an invariant. get_workflow() names the right
    skill. The response is prefixed with this deployment's resolved endpoints."""
    path = os.path.join(SKILLS_DIR, name, "SKILL.md")
    if not os.path.isfile(path):
        available = sorted(
            entry for entry in os.listdir(SKILLS_DIR)
            if os.path.isfile(os.path.join(SKILLS_DIR, entry, "SKILL.md"))
        ) if os.path.isdir(SKILLS_DIR) else []
        raise ValueError(f"unknown skill '{name}' — available: {available}")
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


# --- sources and cameras ---------------------------------------------------

@mcp.tool()
def inspect_source(source_id: int, entity_type: str = "person") -> dict:
    """Everything about ONE source: connection readiness and credential status
    (never the credentials), placement, floor homography and any rich calibration
    import, projection surfaces, its zone views, current complete-sample
    freshness, observed submission rate, what spatial evidence its detections
    carry, and its latest worker heartbeat.

    Use it before calibrating, before drawing zone geometry on that camera, and
    when diagnosing why a source contributes nothing. Use inspect_workspace()
    instead when you need the whole picture."""
    return _req("GET", f"/agent/sources/{source_id}" + _qs({"entity_type": entity_type}))


@mcp.tool()
def configure_source(name: str = "", source_id: int | None = None, kind: str | None = None,
                     connection_mode: str | None = None,
                     connection_management: str | None = None,
                     connection: dict | None = None, credentials: dict | None = None,
                     locator: dict | None = None, capabilities: list[str] | None = None,
                     metadata: dict | None = None) -> dict:
    """Create a logical source, or update it when `source_id` is given.

    Choose `manysight_managed` with a structured `connection` (plus optional
    `credentials`, encrypted at rest) or `external_secret` with
    `locator.local_secret_ref` naming a worker-local secret. A camera URL,
    username, password, or token in `locator` is rejected — that is by design.

    Use it when onboarding a camera, stream, file, or sensor. On update, only the
    arguments you pass are changed; omitted ones keep their stored values, so
    renaming a source never silently resets its connection. It does not place or
    calibrate the source; do that next, before any geometry or fusion work."""
    supplied = {"name": name or None, "kind": kind, "connection_mode": connection_mode,
                "connection_management": connection_management, "connection": connection,
                "credentials": credentials, "locator": locator,
                "capabilities": capabilities, "metadata": metadata}
    if source_id is not None:
        return _req("PUT", f"/sources/{source_id}",
                    {key: value for key, value in supplied.items() if value is not None})
    if not name:
        raise ValueError("a new source needs a name")
    return _req("POST", "/sources", {
        "name": name, "kind": kind or "webcam",
        "connection_mode": connection_mode or "agent_local",
        "connection_management": connection_management or "external_secret",
        "connection": connection or {}, "credentials": credentials,
        "locator": locator or {}, "capabilities": capabilities or [],
        "metadata": metadata or {},
    })


@mcp.tool()
def get_source_connection(source_id: int) -> dict:
    """Explicitly resolve a source's connection material for a local worker that is
    about to open it. May return secrets, and needs no configuration beyond the
    server's own API key if one is set.

    Use it ONLY inside the authorized local process that opens the feed, and pass
    the result straight into capture code in memory. Never log, print, display,
    persist, or copy it into observations, zone metadata, job metadata, generated
    code, or your reply. Ordinary source reads (inspect_source, inspect_workspace)
    are redacted and are what you should use for everything else."""
    return _req("GET", f"/sources/{source_id}/connection")


@mcp.tool()
def plan_frame_capture(source_id: int) -> dict:
    """Get a runnable plan for capturing one frame from a source IN YOUR OWN SHELL,
    plus the pixel-coordinate and calibration context needed to turn that image
    into zone polygons.

    Use it whenever a task needs to SEE a camera — above all before proposing zone
    geometry, so you inspect the real view instead of asking the user for polygon
    coordinates. It returns a plan and geometry context, never image bytes:
    ManySight does not proxy media and this adapter does not process video, so the
    capture runs in your process. Run it, then open the saved image."""
    return _req("GET", f"/agent/sources/{source_id}/frame-capture-plan")


# --- geometry and zones ----------------------------------------------------

@mcp.tool()
def preview_zone(views: list[dict], zone_name: str = "", zone_id: int | None = None,
                 ztype: str = "area") -> dict:
    """Project proposed camera-space polygons onto the shared map and return the
    resulting physical zone preview WITHOUT persisting anything.

    Use it when a physical zone is not yet defined and calibrated cameras provide
    the visual evidence, and use it again after every user correction. Each view is
    {source_id, polygon_px:[{x,y},...], detection_polygon_px?,
    projection_surface_id?, membership_rule?}; pass `zone_id` to preview extending
    an existing zone. Returns each projected polygon with validity, area, and
    calibration revision, the unioned canonical preview, provenance, and warnings.

    Zone geometry is subjective, so show this to the user and get approval before
    commit_zone. Do NOT use it to create per-camera analytical zones — one physical
    region is one canonical zone."""
    return _req("POST", "/agent/zone-preview",
                {"views": views, "zone_name": zone_name, "zone_id": zone_id, "ztype": ztype})


@mcp.tool()
def commit_zone(views: list[dict], approved: bool = False, zone_name: str = "",
                zone_id: int | None = None, ztype: str = "area") -> dict:
    """Persist an approved preview: ONE canonical zone in map metres plus one
    ZoneView per contributing camera, unioning each projected contribution with
    full projection provenance. Pass `zone_id` to extend an existing zone.

    Use it only after the user has approved the geometry preview_zone returned,
    and pass the same views you last previewed. approved=true means the user
    approved — not that you are confident. Cameras that cannot see the region must
    not be included; never invent a polygon to make coverage look complete."""
    return _req("POST", "/agent/zone-commit",
                {"views": views, "approved": approved, "zone_name": zone_name,
                 "zone_id": zone_id, "ztype": ztype})


# --- perception ------------------------------------------------------------

@mcp.tool()
def inspect_perception(entity_type: str = "person", source_ids: list[int] | None = None,
                       require_tracking: bool = True, require_spatial: bool = True,
                       source_fps: float | None = None) -> dict:
    """Can ManySight already answer a question about `entity_type` on these
    sources? Returns per-source availability, healthy/stale/unavailable state,
    observed central submission rate, worker heartbeat, tracking and spatial
    output, multiview readiness, any compatible existing job, and an `action` of
    reuse | extend_coverage | restart_or_repair | perception_missing.

    Also returns `performance`: the achieved processing FPS each worker reports
    against the rate its workload needs. That is a SEPARATE axis from
    availability — a worker tracking at 4 FPS is still healthy perception, but it
    is a readiness warning you must surface rather than calling the worker fine
    because some samples arrived. `readiness_axes` spells out the three
    independent questions: camera available, perception runnable, performance
    capable. Missing CUDA affects only the third.

    Call it BEFORE starting any worker so you reuse healthy perception instead of
    starting a duplicate, and again afterwards to verify the worker is really
    producing complete fresh samples at the right rate. Do not inspect OS
    processes or repository files to answer this. A stale or missing source means
    unknown, never zero."""
    return _req("GET", "/agent/perception" + _qs({
        "entity_type": entity_type, "source_ids": _ids(source_ids),
        "require_tracking": str(require_tracking).lower(),
        "require_spatial": str(require_spatial).lower(),
        "source_fps": source_fps,
    }))


@mcp.tool()
def get_worker_recipe(entity_type: str = "person", tracking: bool = True,
                      source_ids: list[int] | None = None,
                      source_fps: float | None = None) -> dict:
    """The CURRENT worker integration contract, generated from the running
    platform: preferred submission endpoint and envelope, empty-frame semantics,
    source-local identity rules, spatial point meaning, forbidden worker output,
    the rate plan, acceleration and environment guidance,
    registration/heartbeat/stop behaviour, managed-connection workflow, multiview
    prerequisites, the SDK helper, and how to verify.

    `sampling` separates three rates that are easy to conflate: source FPS, local
    processing FPS, and central submission Hz. For tracking workloads it
    recommends a `target_processing_fps` of at least 15 per camera when the
    source supplies it — never quietly configure a tracker at 1-5 FPS on capable
    hardware, and never hard-code a sleep that caps a GPU worker below the
    target. Pass `source_fps` once you have measured it so the plan is computed
    for the real source; a source slower than the floor gets its native rate and
    the limitation reported. `acceleration` is the local GPU/CUDA check to run
    yourself before starting a heavy worker — ManySight cannot see your machine,
    so probe it rather than asking the user whether they have CUDA. CPU is a
    valid fallback, never a reason to call a camera unusable.

    Call it before writing or adapting any worker. It is authoritative — do NOT
    infer the contract from an example script, a demo worker, or an older file
    found in a repository. It describes the protocol, not a model implementation:
    the detector, tracker, and local environment are yours."""
    return _req("GET", "/agent/worker-recipe" + _qs({
        "entity_type": entity_type, "tracking": str(tracking).lower(),
        "source_ids": _ids(source_ids), "source_fps": source_fps,
    }))


@mcp.tool()
def request_worker_state(worker_id: int, desired_state: str) -> dict:
    """Ask a registered worker to move to running, stopped, or restart. The worker
    reads this from its next heartbeat and must obey it cooperatively.

    Use it to stop or restart perception you or a supervisor started. ManySight
    never launches, kills, or relaunches a process, so `restart` does nothing
    without a supervisor. Registration and heartbeating are the worker's own job
    through the SDK — do not register a worker you did not start."""
    return _req("PUT", f"/workers/{worker_id}/desired-state",
                {"desired_state": desired_state})


# --- multiview -------------------------------------------------------------

@mcp.tool()
def configure_multiview_group(name: str = "", source_ids: list[int] | None = None,
                              group_id: int | None = None, enabled: bool | None = None,
                              time_tolerance_s: float | None = None,
                              spatial_gate_m: float | None = None,
                              track_age_s: float | None = None,
                              topology: dict | None = None,
                              configuration: dict | None = None) -> dict:
    """Create, or update when `group_id` is given, an explicit group of cameras
    whose active tracks may be associated into anonymous fused physical tracks.

    Use it when overlapping cameras would otherwise double-count the same person,
    which is a prerequisite for any cross-camera occupancy question. Every member
    must be calibrated into the same metric world frame. Choose gates from
    calibration error, sampling rate, and walking speed rather than guessing a
    large one; on update, omitted gates keep their stored values. Fusion is
    geometric association, never identity or appearance ReID; read its readiness
    back from inspect_workspace() or inspect_perception()."""
    supplied = {"name": name or None, "source_ids": source_ids, "enabled": enabled,
                "time_tolerance_s": time_tolerance_s, "spatial_gate_m": spatial_gate_m,
                "track_age_s": track_age_s, "topology": topology,
                "configuration": configuration}
    if group_id is not None:
        return _req("PATCH", f"/multiview/groups/{group_id}",
                    {key: value for key, value in supplied.items() if value is not None})
    if not name or not source_ids:
        raise ValueError("a new multiview group needs a name and source_ids")
    return _req("POST", "/multiview/groups", {
        "name": name, "source_ids": source_ids,
        "enabled": True if enabled is None else enabled,
        "time_tolerance_s": 0.75 if time_tolerance_s is None else time_tolerance_s,
        "spatial_gate_m": 1.5 if spatial_gate_m is None else spatial_gate_m,
        "track_age_s": 2.0 if track_age_s is None else track_age_s,
        "topology": topology or {}, "configuration": configuration or {},
    })


# --- analytics -------------------------------------------------------------

@mcp.tool()
def run_query(subject: str = "", measures: list[str] | None = None, filters: dict | None = None,
              grouping: dict | None = None, range: dict | None = None,
              comparison: dict | None = None, query_id: int | None = None) -> dict:
    """Execute a deterministic question and return its result — a preview of a
    definition, or a saved query when `query_id` is given.

    subject: detection | measurement | state | fused_entity. Current fused people
    in a zone is subject='fused_entity', measures=['current_occupancy'],
    filters={'group_ids':[g],'zone_ids':[z],'entity_types':['person']} — that is
    fresh fused entities inside the canonical zone, NOT camera bounding boxes and
    NOT distinct raw local tracker IDs. See inspect_workspace()'s
    query_capabilities for the valid subject/measure combinations.

    Use it to validate a definition before saving it, and to read a saved query's
    current value and quality. Returns {shape, dimensions, measures, rows,
    metadata}; `shape` says how to read rows, never which chart to draw. Raw SQL is
    never exposed."""
    if query_id is not None:
        return _req("POST", f"/queries/{query_id}/execute")
    if not subject or not measures:
        raise ValueError("provide query_id, or subject and measures")
    return _req("POST", "/analytics/query", {
        "subject": subject, "measures": measures, "filters": filters or {},
        "grouping": grouping or {}, "range": range or {}, "comparison": comparison or {},
    })


@mcp.tool()
def configure_saved_query(name: str, subject: str = "", measures: list[str] | None = None,
                          filters: dict | None = None, grouping: dict | None = None,
                          question: str = "", default_range: dict | None = None,
                          comparison: dict | None = None, query_id: int | None = None) -> dict:
    """Save one canonical analytical question, or update it when `query_id` is
    given. Dashboards and alert rules reference the saved query by id.

    Use it once a run_query() preview answers the user's question and that
    question needs to persist — because a dashboard or an alert will reference it.
    A saved query is a QUESTION (subject, measures, filters, grouping), not a
    chart. Presentation lives on a dashboard widget, so wanting a different
    rendering never justifies a second saved query — and neither does a rewording.
    Check inspect_workspace()'s saved_queries first and reuse an equivalent
    definition."""
    body = {"name": name, "subject": subject, "measures": measures or [],
            "filters": filters or {}, "grouping": grouping or {}, "question": question,
            "default_range": default_range or {}, "comparison": comparison or {},
            "created_by": "agent"}
    if query_id is None:
        if not subject or not measures:
            raise ValueError("a new saved query needs subject and measures")
        return _req("POST", "/queries", body)
    patch = {k: v for k, v in body.items()
             if k != "created_by" and v not in (None, "", {}, [])}
    return _req("PATCH", f"/queries/{query_id}", patch)


@mcp.tool()
def configure_dashboard(name: str, widgets: list[dict] | None = None,
                        dashboard_id: int | None = None, description: str = "") -> dict:
    """Create or update a generated dashboard and its query-backed widgets in one
    call. Each widget is {query_id, title, presentation, configuration?,
    sort_order?} with presentation number | timeseries | bar | table | heatmap,
    matched to the query's result shape.

    Use it only when the user asked to see, pin, or display something — a saved
    query needs no dashboard to be useful. Widgets are declarative and always
    computed by their saved query: a widget never calculates occupancy or any
    other metric itself, and agents do not generate React or SQL. A widget with
    the same query_id and title is updated rather than duplicated, so re-running
    this is safe."""
    if dashboard_id is None:
        dashboard = _req("POST", "/dashboards",
                         {"name": name, "description": description, "created_by": "agent"})
        dashboard_id = dashboard["id"]
        existing = []
    else:
        _req("PATCH", f"/dashboards/{dashboard_id}", {"name": name, "description": description})
        dashboard = _req("GET", f"/dashboards/{dashboard_id}")
        existing = dashboard.get("widgets", [])
    for index, widget in enumerate(widgets or []):
        match = next((item for item in existing
                      if item.get("query_id") == widget.get("query_id")
                      and item.get("title") == widget.get("title")), None)
        payload = {
            "query_id": widget["query_id"], "title": widget["title"],
            "presentation": widget.get("presentation", "number"),
            "configuration": widget.get("configuration") or {},
            "sort_order": widget.get("sort_order", index),
        }
        if match:
            _req("PATCH", f"/dashboard-widgets/{match['id']}", payload)
        else:
            _req("POST", f"/dashboards/{dashboard_id}/widgets", payload)
    return _req("GET", f"/dashboards/{dashboard_id}")


@mcp.tool()
def configure_alert(name: str, query_id: int | None = None, operator: str = "",
                    value: float | None = None, for_seconds: float = 0,
                    window_s: float | None = None, allow_partial: bool = False,
                    cooldown_s: float = 60, webhook_url: str = "", enabled: bool = True,
                    rule_id: int | None = None, kind: str = "query_condition",
                    params: dict | None = None) -> dict:
    """Create, or update when `rule_id` is given, an edge-triggered alert rule.
    Use it when the user wants to be told about a condition rather than to look at
    a value. Prefer kind='query_condition' with a `query_id` so the alert evaluates
    exactly the saved query a dashboard shows.

    THE OPERATOR IS EXACT AND IS NEVER NORMALIZED. Map the user's own words:
    'more than 2' -> operator='>' value=2; 'at least 2' -> '>='; 'fewer than 3' ->
    '<'; 'at most 3' -> '<='; 'exactly 3' -> '=='. If the phrasing is not clearly
    one of these, ask the user instead of guessing.

    `for_seconds` requires the condition to hold that long before firing.
    Evaluation is periodic, so a quiet zone still fires. Quality is respected: by
    default only `known` evidence can fire or clear an edge, so a stale camera
    never implies zero — set allow_partial=true only if the user accepts partial
    camera coverage. Compatibility kinds (dwell_exceeds, occupancy_exceeds,
    state_alert, event_match, analysis_condition) take `params` instead."""
    condition = None
    if kind == "query_condition":
        if query_id is None or value is None:
            raise ValueError("query_condition needs query_id and value")
        if operator not in COMPARISON_OPERATORS:
            raise ValueError(
                f"operator must be one of {list(COMPARISON_OPERATORS)} and must match the user's "
                "words exactly ('more than N' is '>', 'at least N' is '>=')")
        params = {"query_id": query_id}
        condition = {"operator": operator, "value": value, "for_seconds": for_seconds,
                     "allow_partial": allow_partial}
        if window_s is not None:
            condition["window_s"] = window_s
    body = {"name": name, "kind": kind, "params": params or {}, "condition": condition,
            "webhook_url": webhook_url, "cooldown_s": cooldown_s, "enabled": enabled}
    if rule_id is None:
        return _req("POST", "/alert-rules", body)
    return _req("PUT", f"/alert-rules/{rule_id}", body)


@mcp.tool()
def reset_cameras(confirmed: bool = False, reset_token: str = "") -> dict:
    """DESTRUCTIVE. Remove EVERY camera in the workspace, plus everything that
    exists only because of a camera: connections and stored credentials,
    placement, calibration, projection surfaces, camera zone views, all camera
    observations and current state, multiview groups, and fused and occupancy
    state and history.

    It preserves the workspace, the floor plan, the space dimensions, the
    canonical zones, and the definitions of saved queries, dashboards and alert
    rules — but any alert rule that could no longer fire is disabled, and
    affected saved queries are reported as having stale references.

    Use it ONLY when the user has explicitly asked to remove or reset their
    cameras and set them up again from scratch. Never infer permission from an
    unrelated setup, calibration, or troubleshooting request, and never use it to
    tidy up sources that merely look stale — removing a camera is a different
    decision from not selecting it.

    Defaults to a dry run. Call it first with confirmed=false, show the user the
    impact counts, and only call it with confirmed=true after they agree. Pass
    the preview's `reset_token` so a camera added in between makes the reset fail
    instead of silently removing something the user never saw."""
    body: dict = {"dry_run": not confirmed}
    if confirmed:
        body["confirmation"] = "RESET CAMERAS"
        if reset_token:
            body["reset_token"] = reset_token
    return _req("POST", "/workspace/reset-cameras", body)


# ===========================================================================
# LEGACY / INTERNAL HANDLERS
#
# Still implemented, still importable, still exercised by tests, and still
# re-registerable with MANYSIGHT_MCP_LEGACY_TOOLS=1. Not advertised by default:
# the curated tools above supersede them, and a sixty-tool list is the problem
# this module exists to solve. REST/SDK remain the complete interface.
# ===========================================================================

def get_platform_config() -> dict:
    """Return the authoritative dashboard, REST, OpenAPI, agent-guide, discovery,
    and MCP endpoints resolved for this ManySight deployment."""
    return _req("GET", "/platform-config")


def list_skills() -> list[dict]:
    """Superseded by list_workflows(). The skill index; get_skill() still serves
    the files themselves."""
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


def list_sources() -> list[dict]:
    """Superseded by inspect_workspace(). Raw source list with safe connection
    metadata, capabilities, latest worker runtime, freshness, placement, calibration."""
    return _req("GET", "/sources")


def get_source(source_id: int) -> dict:
    """Superseded by inspect_source(). Raw single-source metadata; never credentials."""
    return _req("GET", f"/sources/{source_id}")


def create_source(name: str, kind: str = "webcam", connection_mode: str = "agent_local",
                  locator: dict | None = None, capabilities: list[str] | None = None,
                  metadata: dict | None = None,
                  connection_management: str = "external_secret",
                  connection: dict | None = None, credentials: dict | None = None) -> dict:
    """Superseded by configure_source()."""
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


def update_source(source_id: int, patch: dict) -> dict:
    """Superseded by configure_source(source_id=...). Omitted credentials are
    preserved; clear_credentials=true removes them."""
    return _req("PUT", f"/sources/{source_id}", patch)


def delete_source(source_id: int) -> dict:
    """Delete a logical source and its geometry. Historical observations remain
    queryable. Destructive; not part of the curated surface."""
    return _req("DELETE", f"/sources/{source_id}")


def get_store_map() -> dict:
    """Superseded by inspect_workspace(). Floor plan, zones, surfaces, views, cameras."""
    store = _req("GET", "/store")
    store["zones"] = _req("GET", "/zones")
    store["projection_surfaces"] = _req("GET", "/projection-surfaces")
    store["zone_views"] = _req("GET", "/zone-views")
    store["cameras"] = [
        {k: s[k] for k in ("id", "name", "kind", "observation_status", "placement", "calibrated")}
        for s in _req("GET", "/sources")
    ]
    return store


def list_zones() -> list[dict]:
    """Superseded by inspect_workspace(). All canonical zones in map metres."""
    return _req("GET", "/zones")


def get_zone(zone_id: int) -> dict:
    """Canonical Polygon/MultiPolygon geometry with camera-extension provenance."""
    return _req("GET", f"/zones/{zone_id}")


def update_zone(zone_id: int, patch: dict) -> dict:
    """Update a canonical zone's metadata or metric footprint. Camera-specific
    polygons belong in a zone view."""
    return _req("PUT", f"/zones/{zone_id}", patch)


def create_zone(name: str, ztype: str = "area",
                polygon_map: list[dict] | None = None,
                polygon_px: list[dict] | None = None,
                source_id: int | None = None) -> dict:
    """Superseded by preview_zone()/commit_zone(), which preview projection before
    persisting and create the camera zone views too. Direct creation from an
    explicit map-metre polygon (polygon_map) or one camera polygon
    (polygon_px + source_id)."""
    return _req("POST", "/zones", {"name": name, "ztype": ztype, "polygon": polygon_map,
                                   "polygon_px": polygon_px, "source_id": source_id})


def project_points(source_id: int, points: list[dict], surface_id: int | None = None) -> dict:
    """Project camera pixels to map metres on the floor (surface_id omitted) or a named
    elevated plane. Never compensate for physical height by subtracting map Y."""
    return _req("POST", f"/sources/{source_id}/project",
                {"points": points, "surface_id": surface_id})


def unproject_points(source_id: int, points: list[dict], surface_id: int | None = None) -> dict:
    """Project map-metre points into a camera frame on the selected plane."""
    return _req("POST", f"/sources/{source_id}/unproject",
                {"points": points, "surface_id": surface_id})


def list_projection_surfaces(source_id: int | None = None) -> list[dict]:
    """List named source-specific planes such as mattress, table, shelf, or conveyor."""
    return _req("GET", "/projection-surfaces" + _qs({"source_id": source_id}))


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


def update_projection_surface(surface_id: int, patch: dict) -> dict:
    """Update a named plane and recompute its homography. Its revision increments;
    existing observations keep the surface revision used when they were ingested."""
    return _req("PUT", f"/projection-surfaces/{surface_id}", patch)


def delete_projection_surface(surface_id: int) -> dict:
    """Delete an unused named plane. Remove or repoint dependent zone views first."""
    return _req("DELETE", f"/projection-surfaces/{surface_id}")


def list_zone_views(source_id: int | None = None, zone_id: int | None = None) -> list[dict]:
    """Superseded by inspect_source()/inspect_workspace(). Per-camera zone geometry."""
    return _req("GET", "/zone-views" + _qs({"source_id": source_id, "zone_id": zone_id}))


def create_zone_view(zone_id: int, source_id: int, outer_polygon_px: list[dict],
                     detection_polygon_px: list[dict] | None = None,
                     projection_surface_id: int | None = None,
                     membership_rule: str = "point", threshold: float = 0.5,
                     min_keypoints: int = 1) -> dict:
    """Superseded by commit_zone(). One camera's view of a canonical zone."""
    return _req("POST", "/zone-views", {
        "zone_id": zone_id, "source_id": source_id,
        "outer_polygon_px": outer_polygon_px,
        "detection_polygon_px": detection_polygon_px,
        "projection_surface_id": projection_surface_id,
        "membership_rule": membership_rule, "threshold": threshold,
        "min_keypoints": min_keypoints,
    })


def update_zone_view(view_id: int, patch: dict) -> dict:
    """Update a camera ROI, decision rule, or plane. The view revision increments."""
    return _req("PUT", f"/zone-views/{view_id}", patch)


def delete_zone_view(view_id: int) -> dict:
    """Delete one camera-specific view without deleting the canonical map zone."""
    return _req("DELETE", f"/zone-views/{view_id}")


def extend_zone_from_view(view_id: int, polygon: str = "outer") -> dict:
    """Superseded by commit_zone(). Explicitly union one projected zone-view
    polygon into the canonical zone; creating a view never does this implicitly."""
    return _req("POST", f"/zone-views/{view_id}/extend-zone", {"polygon": polygon})


def list_calibrations(source_id: int | None = None) -> list[dict]:
    """List provider-neutral rich calibrations and their derived floor homographies."""
    return _req("GET", "/calibrations" + _qs({"source_id": source_id}))


def import_calibration(source_id: int, projection_matrix: list, world_frame: dict,
                       units: str = "m", provider: str = "generic",
                       world_to_map_transform: list | None = None,
                       ground_plane_z: float = 0.0, frame_w: int | None = None,
                       frame_h: int | None = None, distortion: list | dict | None = None,
                       intrinsics: dict | None = None, extrinsics: dict | None = None,
                       verification_points: list[dict] | None = None) -> dict:
    """Import a 3x4 world-to-pixel calibration (generic, NVIDIA MV3DT, or AMC).
    World coordinates must be metres with explicit axis/frame metadata."""
    return _req("POST", "/calibrations/import", {
        "source_id": source_id, "provider": provider,
        "projection_matrix": projection_matrix, "world_frame": world_frame,
        "world_to_map_transform": world_to_map_transform or [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
        "units": units, "ground_plane_z": ground_plane_z,
        "frame_w": frame_w, "frame_h": frame_h,
        "distortion": distortion or [], "intrinsics": intrinsics or {},
        "extrinsics": extrinsics or {}, "verification_points": verification_points or [],
    })


def list_multiview_groups() -> list[dict]:
    """Superseded by inspect_workspace(). Explicit calibrated fusion groups."""
    return _req("GET", "/multiview/groups")


def create_multiview_group(name: str, source_ids: list[int], enabled: bool = True,
                           time_tolerance_s: float = 0.75, spatial_gate_m: float = 1.5,
                           track_age_s: float = 2.0, topology: dict | None = None,
                           configuration: dict | None = None) -> dict:
    """Superseded by configure_multiview_group()."""
    return _req("POST", "/multiview/groups", {
        "name": name, "source_ids": source_ids, "enabled": enabled,
        "time_tolerance_s": time_tolerance_s, "spatial_gate_m": spatial_gate_m,
        "track_age_s": track_age_s, "topology": topology or {},
        "configuration": configuration or {},
    })


def update_multiview_group(group_id: int, patch: dict) -> dict:
    """Superseded by configure_multiview_group(group_id=...)."""
    return _req("PATCH", f"/multiview/groups/{group_id}", patch)


def get_multiview_status(group_id: int | None = None) -> dict:
    """Superseded by inspect_perception(). Fused current entities plus per-group
    source freshness and quality."""
    return _req("GET", "/multiview/current" + _qs({"group_id": group_id}))


def register_job(name: str, description: str = "", source_ids: list[int] | None = None,
                 event_types: list[str] | None = None) -> dict:
    """Register an analysis job. A worker normally does this itself through the
    SDK; see get_worker_recipe()."""
    return _req("POST", "/jobs", {
        "name": name, "description": description,
        "source_ids": source_ids or [], "event_types": event_types or [],
    })


def list_jobs() -> list[dict]:
    """Superseded by inspect_perception(). Jobs with their latest worker instance."""
    return _req("GET", "/jobs")


def list_workers(job_id: int | None = None) -> list[dict]:
    """Superseded by inspect_perception(). Worker instances; effective_status
    becomes stale without heartbeats."""
    return _req("GET", "/workers" + _qs({"job_id": job_id}))


def register_worker(job_id: int, name: str = "", version: str = "",
                    worker_id: str | None = None, config: dict | None = None) -> dict:
    """Register a concrete worker instance. The worker process should do this
    itself through the SDK; never register a worker you did not start."""
    return _req("POST", "/workers", {
        "job_id": job_id, "name": name, "version": version,
        "worker_id": worker_id, "config": config or {},
    })


def heartbeat_worker(worker_id: int, status: str = "running",
                     metrics: dict | None = None, last_error: str = "") -> dict:
    """One lifecycle heartbeat. The worker process owns this; report source_fps,
    processing_fps, submission_hz, device and precision in metrics so
    inspect_perception can tell a healthy worker from a slow one."""
    return _req("POST", f"/workers/{worker_id}/heartbeat", {
        "status": status, "metrics": metrics or {}, "last_error": last_error,
    })


def get_observation_contract() -> dict:
    """Superseded by get_worker_recipe(). The machine-readable observation kinds,
    required fields, and forbidden kinds."""
    return _req("GET", "/observations/contract")


def submit_detection_sample(source_id: int, sample_id: str, timestamp: float,
                            detections: list[dict] | None = None,
                            entity_type: str = "person", frame_index: int | None = None,
                            worker_id: int | None = None, job_id: int | None = None) -> dict:
    """Submit one atomic processed frame. Workers should post this themselves from
    their own process (see get_worker_recipe()); routing high-rate perception
    through an MCP client is not the intended path. detections=[] is a known zero."""
    return _req("POST", "/detection-samples", {
        "schema_version": 2, "source_id": source_id, "sample_id": sample_id,
        "timestamp": timestamp, "entity_type": entity_type,
        "detections": detections or [], "frame_index": frame_index,
        "worker_id": worker_id, "job_id": job_id,
    })


def submit_observations(observations: list[dict], job_id: int | None = None) -> dict:
    """Raw schema-v2 ingestion (max 5000). Workers submit from their own process;
    see get_worker_recipe() for the current contract and detection-samples for the
    preferred detection path. Never send zone_id/zone or the legacy derived kinds
    zone_enter/zone_exit/zone_dwell/state_change/count — they are rejected."""
    return _req("POST", "/observations/batch", {"job_id": job_id, "observations": observations})


def list_observations(since: float | None = None, until: float | None = None,
                      kind: str | None = None, source_id: int | None = None,
                      entity_id: str | None = None, name: str | None = None,
                      label: str | None = None, zone_id: int | None = None,
                      cursor: str | None = None, limit: int = 100) -> dict:
    """Query stored observations, newest first, including the zone/projection
    assigned at ingestion. Page with next_cursor."""
    return _req("GET", "/observations" + _qs({
        "since": since, "until": until, "kind": kind, "source_id": source_id,
        "entity_id": entity_id, "name": name, "label": label, "zone_id": zone_id,
        "cursor": cursor, "limit": limit,
    }))


def get_latest_observations(kind: str | None = None, source_id: int | None = None,
                            zone_id: int | None = None, name: str | None = None) -> dict:
    """Current-value read models derived from raw observations: latest entities,
    latest measurement per key, current state label and duration."""
    return _req("GET", "/observations/latest" + _qs({
        "kind": kind, "source_id": source_id, "zone_id": zone_id, "name": name}))


def get_latest_detection_frames(entity_type: str = "person", source_id: int | None = None) -> dict:
    """Each source's latest complete processed frame. Scene contents persist until a
    newer complete sample arrives; `stale` describes freshness without changing them."""
    return _req("GET", "/observations/latest-frames" + _qs({
        "entity_type": entity_type, "source_id": source_id}))


def list_current_entities(entity_type: str = "person", source_id: int | None = None) -> dict:
    """Source-local entities from each latest complete sample — the per-camera debug
    view, NOT deduplicated cross-camera state."""
    return get_latest_detection_frames(entity_type=entity_type, source_id=source_id)


def list_current_fused_entities(group_id: int | None = None,
                                entity_type: str = "person",
                                zone_id: int | None = None) -> dict:
    """Anonymous fused entities and their member evidence for a multiview group."""
    return _req("GET", "/multiview/current" + _qs({
        "entity_type": entity_type, "group_id": group_id, "zone_id": zone_id}))


def submit_events(events: list[dict], job_id: int | None = None) -> dict:
    """LEGACY event contract, retained for historical compatibility only. New work
    uses detection samples; see get_worker_recipe()."""
    return _req("POST", "/events", {"job_id": job_id, "events": events})


def get_events(since: float | None = None, until: float | None = None,
               event_type: str | None = None, zone_id: int | None = None,
               source_id: int | None = None, job_id: int | None = None,
               track_id: str | None = None, label: str | None = None,
               cursor: str | None = None, limit: int = 100) -> dict:
    """Query stored legacy events, newest first. Page with next_cursor."""
    return _req("GET", "/events" + _qs({
        "since": since, "until": until, "event_type": event_type, "zone_id": zone_id,
        "source_id": source_id, "job_id": job_id, "track_id": track_id, "label": label,
        "cursor": cursor, "limit": limit,
    }))


def get_analytics(kind: str, params: dict | None = None) -> dict:
    """LEGACY per-kind analytics endpoints. Prefer run_query()."""
    allowed = {"summary", "heatmap", "dwell", "occupancy", "counts", "transitions", "states"}
    if kind not in allowed:
        raise ValueError(f"kind must be one of {sorted(allowed)}")
    qs = urllib.parse.urlencode(params or {})
    return _req("GET", f"/analytics/{kind}" + (f"?{qs}" if qs else ""))


def list_query_capabilities() -> dict:
    """Superseded by inspect_workspace().analytics.query_capabilities. The full
    subject/measure/filter/grouping capability document."""
    return _req("GET", "/queries/capabilities")


def query_data(subject: str, measures: list[str], filters: dict | None = None,
               grouping: dict | None = None, range: dict | None = None,
               comparison: dict | None = None) -> dict:
    """Superseded by run_query()."""
    return _req("POST", "/analytics/query", {
        "subject": subject, "measures": measures, "filters": filters or {},
        "grouping": grouping or {}, "range": range or {}, "comparison": comparison or {},
    })


def list_saved_queries() -> list[dict]:
    """Superseded by inspect_workspace(). Canonical saved query definitions."""
    return _req("GET", "/queries?include_hidden=true")


def create_saved_query(name: str, subject: str, measures: list[str],
                       filters: dict | None = None, grouping: dict | None = None,
                       question: str = "", default_range: dict | None = None,
                       comparison: dict | None = None) -> dict:
    """Superseded by configure_saved_query()."""
    return _req("POST", "/queries", {
        "name": name, "subject": subject, "measures": measures,
        "filters": filters or {}, "grouping": grouping or {}, "question": question,
        "default_range": default_range or {}, "comparison": comparison or {},
        "created_by": "agent",
    })


def update_saved_query(query_id: int, patch: dict) -> dict:
    """Superseded by configure_saved_query(query_id=...)."""
    return _req("PATCH", f"/queries/{query_id}", patch)


def delete_saved_query(query_id: int) -> dict:
    """Delete an unreferenced saved query. Referenced queries return HTTP 409."""
    return _req("DELETE", f"/queries/{query_id}")


def execute_saved_query(query_id: int) -> dict:
    """Superseded by run_query(query_id=...)."""
    return _req("POST", f"/queries/{query_id}/execute")


def list_dashboards() -> list[dict]:
    """Superseded by inspect_workspace(). Generated dashboards and their widgets."""
    return _req("GET", "/dashboards")


def create_dashboard(name: str, description: str = "") -> dict:
    """Superseded by configure_dashboard()."""
    return _req("POST", "/dashboards", {
        "name": name, "description": description, "created_by": "agent",
    })


def update_dashboard(dashboard_id: int, patch: dict) -> dict:
    """Superseded by configure_dashboard(dashboard_id=...)."""
    return _req("PATCH", f"/dashboards/{dashboard_id}", patch)


def add_dashboard_widget(dashboard_id: int, query_id: int, title: str,
                         presentation: str, configuration: dict | None = None,
                         sort_order: int = 0) -> dict:
    """Superseded by configure_dashboard(widgets=[...])."""
    return _req("POST", f"/dashboards/{dashboard_id}/widgets", {
        "query_id": query_id, "title": title, "presentation": presentation,
        "configuration": configuration or {}, "sort_order": sort_order,
    })


def update_dashboard_widget(widget_id: int, patch: dict) -> dict:
    """Superseded by configure_dashboard(widgets=[...])."""
    return _req("PATCH", f"/dashboard-widgets/{widget_id}", patch)


def delete_dashboard(dashboard_id: int) -> dict:
    """Delete a dashboard and its widgets; saved queries and observations are preserved."""
    return _req("DELETE", f"/dashboards/{dashboard_id}")


def create_alert_rule(name: str, kind: str, params: dict | None = None, analysis: dict | None = None,
                      condition: dict | None = None, webhook_url: str = "", cooldown_s: float = 60) -> dict:
    """Superseded by configure_alert(). Compatibility kinds: dwell_exceeds,
    occupancy_exceeds, state_alert, event_match, analysis_condition, query_condition."""
    return _req("POST", "/alert-rules", {"name": name, "kind": kind, "params": params or {},
                                         "analysis": analysis, "condition": condition,
                                         "webhook_url": webhook_url, "cooldown_s": cooldown_s})


_INSIGHT_DATASET_TO_SUBJECT = {
    "summary": ("detection", ["distinct_entities"]), "heatmap": ("detection", ["density"]),
    "dwell": ("detection", ["visits", "average_dwell", "total_dwell"]),
    "occupancy": ("detection", ["active_entities"]), "counts": ("measurement", ["latest"]),
    "transitions": ("detection", ["transition_count"]), "states": ("state", ["duration"]),
}


def register_insight(title: str, block: str, dataset: str, params: dict | None = None,
                     question: str = "", unit: str = "", limitations: str = "",
                     pinned: bool = False) -> dict:
    """RETIRED compatibility adapter. Use configure_saved_query. Translates the old
    block+dataset+params model into the closest saved-query equivalent."""
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


def list_insights() -> list[dict]:
    """RETIRED, read-only. Historical insight_definitions rows only."""
    return _req("GET", "/insights?include_hidden=true")


def delete_insight(insight_id: int) -> dict:
    """RETIRED. Delete a historical insight_definitions row (cleanup only)."""
    return _req("DELETE", f"/insights/{insight_id}")


# Order matters only for a stable advertised list in legacy mode.
LEGACY_TOOLS = [
    get_platform_config, list_skills,
    list_sources, get_source, create_source, update_source, delete_source,
    get_store_map, list_zones, get_zone, update_zone, create_zone,
    project_points, unproject_points,
    list_projection_surfaces, create_projection_surface, update_projection_surface,
    delete_projection_surface,
    list_zone_views, create_zone_view, update_zone_view, delete_zone_view, extend_zone_from_view,
    list_calibrations, import_calibration,
    list_multiview_groups, create_multiview_group, update_multiview_group, get_multiview_status,
    register_job, list_jobs, list_workers, register_worker, heartbeat_worker,
    get_observation_contract, submit_detection_sample, submit_observations,
    list_observations, get_latest_observations, get_latest_detection_frames,
    list_current_entities, list_current_fused_entities,
    submit_events, get_events, get_analytics,
    list_query_capabilities, query_data, list_saved_queries, create_saved_query,
    update_saved_query, delete_saved_query, execute_saved_query,
    list_dashboards, create_dashboard, update_dashboard, add_dashboard_widget,
    update_dashboard_widget, delete_dashboard, create_alert_rule,
]

PUBLIC_TOOLS = [
    "inspect_workspace", "list_workflows", "get_workflow", "get_skill",
    "inspect_source", "configure_source", "get_source_connection", "plan_frame_capture",
    "preview_zone", "commit_zone",
    "inspect_perception", "get_worker_recipe", "request_worker_state",
    "configure_multiview_group",
    "run_query", "configure_saved_query", "configure_dashboard", "configure_alert",
    "reset_cameras",
]

if LEGACY_TOOL_MODE:
    for _legacy in LEGACY_TOOLS:
        mcp.tool()(_legacy)


if __name__ == "__main__":
    transport = os.environ.get("MANYSIGHT_MCP_TRANSPORT", "stdio")
    if transport not in {"stdio", "streamable-http"}:
        raise SystemExit("MANYSIGHT_MCP_TRANSPORT must be stdio or streamable-http")
    run_server(
        mcp, transport, host=MCP_HOST, port=MCP_PORT,
        dns_rebinding_protection=MCP_DNS_REBINDING_PROTECTION,
        allowed_hosts=MCP_ALLOWED_HOSTS, allowed_origins=MCP_ALLOWED_ORIGINS,
    )
