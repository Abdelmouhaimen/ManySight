"""Isolated guided-demo workspaces and deterministic cached replay."""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import shutil
import tempfile
import time
import uuid
from functools import lru_cache
from pathlib import Path

from fastapi import HTTPException

from .. import db

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "demo" / "fixtures" / "nvidia_mv3dt_yolo11n_bytetrack.jsonl"
RECIPE = ROOT / "demo" / "fixtures" / "nvidia_mv3dt_recipe.json"
DERIVED_CACHE = ROOT / "demo" / "fixtures" / "nvidia_mv3dt_derived_replay.json"
DERIVATION_FILES = [
    ROOT / "server" / "routers" / "observations.py",
    ROOT / "server" / "services" / "enrich.py",
    ROOT / "server" / "services" / "current_state.py",
    ROOT / "server" / "services" / "multiview.py",
    ROOT / "server" / "routers" / "analytics_query.py",
    ROOT / "server" / "services" / "alert_engine.py",
]
UPSTREAM_ARCHIVE = (
    "https://github.com/NVIDIA/DeepStream/raw/refs/heads/main/"
    "src/apps/reference_apps/deepstream-tracker-3d-multi-view/assets/datasets.zip"
)
SESSION_ROOT = Path(tempfile.gettempdir()) / "storelens-demo-sessions"
ACTIVE_STATES = {"ready", "running", "paused"}
logger = logging.getLogger("storelens.demo")


def _normal_rows(sql: str, args=()) -> list[dict]:
    with db.using_database(db.DB_PATH):
        return db.q(sql, args)


def _normal_row(sql: str, args=()) -> dict | None:
    with db.using_database(db.DB_PATH):
        return db.q1(sql, args)


def _normal_ex(sql: str, args=()) -> int:
    with db.using_database(db.DB_PATH):
        return db.ex(sql, args)


def load_recipe() -> dict:
    return json.loads(RECIPE.read_text(encoding="utf-8"))


def load_fixture() -> tuple[dict, list[dict]]:
    if not FIXTURE.is_file():
        raise HTTPException(503, "the committed replay fixture is not available")
    rows = [json.loads(line) for line in FIXTURE.read_text(encoding="utf-8").splitlines() if line.strip()]
    return rows[0], rows[1:]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_hash(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def derivation_hash() -> str:
    """Hash the platform code paths that produce the committed replay timeline."""
    digest = hashlib.sha256()
    for path in DERIVATION_FILES:
        digest.update(path.relative_to(ROOT).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


@lru_cache(maxsize=1)
def load_derived_cache() -> dict:
    """Load and verify the committed StoreLens-derived playback artifact."""
    if not DERIVED_CACHE.is_file():
        raise HTTPException(503, "the derived guided-demo replay cache is not available")
    cache = json.loads(DERIVED_CACHE.read_text(encoding="utf-8"))
    metadata = cache.get("metadata", {})
    recipe = load_recipe()
    expected = {
        "type": "storelens_derived_replay_cache",
        "recipe_version": recipe["recipe_version"],
        "raw_fixture_sha256": _sha256(FIXTURE),
        "recipe_sha256": _sha256(RECIPE),
        "geometry_hash": _canonical_hash(cache.get("geometry")),
        "fusion_config_hash": _canonical_hash(recipe["multiview"]),
        "derivation_code_hash": derivation_hash(),
        "sample_rate_hz": float(recipe["replay"]["sample_rate_hz"]),
        "source_fps": recipe["frame"]["fps"],
        "payload_sha256": _canonical_hash({
            "geometry": cache.get("geometry"), "timeline": cache.get("timeline")
        }),
    }
    mismatches = [key for key, value in expected.items() if metadata.get(key) != value]
    if mismatches:
        raise HTTPException(
            503,
            "guided-demo replay cache is stale or invalid; rebuild it "
            f"(mismatch: {', '.join(mismatches)})",
        )
    if not cache.get("timeline"):
        raise HTTPException(503, "guided-demo replay cache has no derived samples")
    return cache


def resolve_asset_root(explicit: str | None = None) -> Path | None:
    candidates = [
        explicit,
        os.environ.get("STORELENS_DEMO_ASSET_DIR"),
        str(ROOT / "data" / "demo-assets" / "datasets" / "mtmc_12cam"),
        str(Path(tempfile.gettempdir()) / "storelens-demo-assets" / "datasets" / "mtmc_12cam"),
    ]
    for value in candidates:
        if not value:
            continue
        root = Path(value).expanduser().resolve()
        videos = root / "videos"
        if (
            (root / "map.png").is_file()
            and all((videos / f"Warehouse_Synthetic_Cam{i:03d}.mp4").is_file() for i in range(1, 5))
            and (root / "camInfo" / "Warehouse_Synthetic_Cam012.yml").is_file()
        ):
            return root
    return None


def asset_status() -> dict:
    root = resolve_asset_root()
    cache_ready = True
    cache_error = None
    try:
        cache_metadata = load_derived_cache()["metadata"]
    except HTTPException as exc:
        cache_ready = False
        cache_metadata = None
        cache_error = str(exc.detail)
    return {
        "available": root is not None and cache_ready,
        "dataset": "NVIDIA DeepStream MV3DT mtmc_12cam synthetic warehouse sample (cameras 1-4)",
        "download_url": UPSTREAM_ARCHIVE,
        "install_command": "python demo/fetch_nvidia_mv3dt.py",
        "environment_variable": "STORELENS_DEMO_ASSET_DIR",
        "redistributed_by_storelens": False,
        "bird_view_available": root is not None,
        "derived_cache_available": cache_ready,
        "derived_cache": cache_metadata,
        "derived_cache_error": cache_error,
    }


def _session_row(session_id: str) -> dict:
    row = _normal_row("SELECT * FROM demo_sessions WHERE id=?", (session_id,))
    if not row:
        raise HTTPException(404, "demo session not found")
    return row


def _session_cache_current(row: dict) -> bool:
    stored = db.jload(row.get("result_json"), {}).get("derived_replay", {})
    return stored == load_derived_cache()["metadata"]


def session_database(session_id: str) -> str | None:
    row = _normal_row(
        "SELECT status,workspace_path,result_json FROM demo_sessions WHERE id=?",
        (session_id,),
    )
    if not row or row["status"] not in ACTIVE_STATES or not _session_cache_current(row):
        return None
    path = Path(row["workspace_path"]).resolve()
    root = SESSION_ROOT.resolve()
    if root not in path.parents or not path.is_file():
        return None
    return str(path)


def _clock(row: dict, now: float | None = None) -> dict:
    current = db.now() if now is None else float(now)
    duration = max(float(row["duration_s"] or 0), 0.001)
    absolute = int(row["playback_epoch"] or 0) * duration + float(row["playback_position_s"] or 0)
    if row["status"] == "running" and row["playback_started_at"] is not None:
        absolute = max(0.0, current - float(row["playback_started_at"]))
    epoch = int(absolute // duration)
    position = absolute - epoch * duration
    return {
        "server_now": current,
        "absolute_s": absolute,
        "position_s": position,
        "epoch": epoch,
        "status": row["status"],
    }


def _public(row: dict) -> dict:
    clock = _clock(row)
    status = row["status"]
    position = clock["position_s"]
    epoch = clock["epoch"]
    usage = {"database_bytes": 0, "observations": 0, "fused_observations": 0,
             "retained_epochs": int(row["retained_epochs"])}
    workspace = Path(row["workspace_path"])
    if workspace.is_file():
        usage["database_bytes"] = workspace.stat().st_size
        with db.using_database(str(workspace)):
            usage["observations"] = db.q1("SELECT COUNT(*) n FROM events")["n"]
            usage["fused_observations"] = db.q1("SELECT COUNT(*) n FROM fused_observations")["n"]
    result = db.jload(row["result_json"], {})
    recipe = load_recipe()
    source_ids = result.get("source_ids", {})
    # A camera only carries a zone trace once that zone view really exists in the
    # workspace, so a guided walkthrough shows each contribution appearing.
    traced_sources = set()
    if result.get("zone_id"):
        workspace = Path(row["workspace_path"])
        if workspace.is_file():
            with db.using_database(str(workspace)):
                traced_sources = {view["source_id"] for view in db.q(
                    "SELECT source_id FROM zone_views WHERE zone_id=?", (result["zone_id"],))}
    result["camera_overlays"] = {
        camera["key"]: {
            "camera_key": camera["key"],
            "source_id": source_ids.get(camera["key"]),
            "frame_width": recipe["frame"]["width"],
            "frame_height": recipe["frame"]["height"],
            "fps": recipe["frame"]["fps"],
            "zones": [{
                "name": recipe["zone"]["name"],
                "color": recipe["zone"]["color"],
                "polygons_px": camera.get("zone_view_polygons_px")
                or [camera["zone_view_px"]],
            }] if camera.get("zone_view_px")
                and source_ids.get(camera["key"]) in traced_sources else [],
        }
        for camera in recipe["cameras"]
    }
    return {
        "id": row["id"], "status": status, "mode": row["mode"],
        "recipe_version": row["recipe_version"], "playback_epoch": epoch,
        "playback_position_s": position, "duration_s": row["duration_s"],
        "action_log": db.jload(row["action_log_json"], []),
        "result": result,
        "created_at": row["created_at"], "updated_at": row["updated_at"],
        "demo_workspace": True, "resource_usage": usage,
        "master_clock": clock,
    }


def get_session(session_id: str) -> dict:
    row = _session_row(session_id)
    if row["recipe_version"] != load_recipe()["recipe_version"]:
        raise HTTPException(409, "demo session belongs to an obsolete dataset recipe; start a new demo")
    if not _session_cache_current(row):
        raise HTTPException(409, "demo session belongs to an obsolete derived cache; start a new demo")
    return _public(row)


def active_session() -> dict | None:
    row = _normal_row(
        "SELECT * FROM demo_sessions WHERE status IN ('ready','running','paused') "
        "AND recipe_version=? ORDER BY created_at DESC LIMIT 1",
        (load_recipe()["recipe_version"],),
    )
    return _public(row) if row and _session_cache_current(row) else None


def _action(log: list, name: str, result: dict, explanation: str) -> None:
    log.append({"name": name, "status": "completed", "result": result, "explanation": explanation})


def _setup_space(path: Path, session_id: str, base_url: str) -> tuple[list, dict]:
    """Create the camera and space setup: mapped space, four sources, placements,
    imported calibrations, and the calibrated multiview group.

    This is everything a promotion may copy. The monitored zone and the analyses
    that answer the demo's question are applied separately (see apply_request), so
    a guided walkthrough can create them at the step that explains them.
    """
    from ..routers import calibrations, multiview, sources, store

    recipe = load_recipe()
    log: list[dict] = []
    _action(log, "Inspect workspace", {"workspace": "isolated demo"},
            "Confirmed that guided-demo changes are isolated from the normal StoreLens workspace.")
    with db.using_database(str(path)):
        configured = store.update_store(store.StorePatch(
            name=recipe["store"]["name"], space_type=recipe["store"]["space_type"],
            environment="demo", width_m=recipe["store"]["width_m"],
            height_m=recipe["store"]["height_m"], map=recipe["store"]["map"],
        ))
        _action(log, "Create temporary mapped space", {"store_id": configured["id"]},
                "Created in the isolated demo SQLite workspace.")
        source_ids: dict[str, int] = {}
        for camera in recipe["cameras"]:
            media_url = f"{base_url}/api/v1/demo/media/{camera['key']}.mp4?demo_session={session_id}"
            source = sources.create_source(sources.SourceIn(
                name=camera["name"], kind="http", connection_management="storelens_managed",
                connection={"url": media_url, "auth_type": "none"}, capabilities=["video"],
                metadata={"demo_fixture_source_key": camera["key"], "producer_kind": "replay"},
            ))
            source_ids[camera["key"]] = source["id"]
            sources.set_placement(source["id"], sources.Placement(**camera["placement"]))
            imported = calibrations.import_calibration(calibrations.CalibrationImportIn(
                source_id=source["id"], provider="nvidia_mv3dt",
                projection_matrix=camera["projection_matrix"],
                world_to_map_transform=recipe["world_to_map_transform"], units="m",
                world_frame=recipe["world_frame"], frame_w=recipe["frame"]["width"],
                frame_h=recipe["frame"]["height"],
            ))
            _action(log, f"Import {camera['name']} calibration",
                    {"source_id": source["id"], "calibration_id": imported["id"],
                     "verification": imported["verification"]},
                    "Imported a real 3x4 world-to-pixel matrix and derived the floor homography.")
        _action(log, "Open four synchronized camera captures",
                {"camera_keys": list(source_ids), "source_fps": recipe["frame"]["fps"]},
                "Opened the four native NVIDIA recordings on one shared media timeline.")
        group = multiview.create_group(multiview.MultiviewGroupIn(
            name=recipe["multiview"]["name"], source_ids=list(source_ids.values()),
            time_tolerance_s=recipe["multiview"]["time_tolerance_s"],
            spatial_gate_m=recipe["multiview"]["spatial_gate_m"],
            track_age_s=recipe["multiview"]["track_age_s"],
            configuration={"producer": "fixture_replay", "appearance_reid": False},
        ))
        _action(log, "Create calibrated multiview group", {"group_id": group["id"]},
                "Enabled StoreLens-owned geometry/time association for anonymous source-local tracks.")
    return log, {"source_ids": source_ids, "group_id": group["id"]}


def _stage_zone_seed(recipe: dict, result: dict, log: list) -> None:
    """The first camera's floor trace, projected into a canonical zone."""
    from ..routers import geometry, zones

    seed_key = recipe["zone"]["seed_camera_key"]
    seed_camera = next(camera for camera in recipe["cameras"] if camera["key"] == seed_key)
    seed_polygon = [{"x": p[0], "y": p[1]} for p in seed_camera["zone_view_px"]]
    monitored_zone = zones.create_zone(zones.ZoneIn(
        name=recipe["zone"]["name"], ztype=recipe["zone"]["ztype"],
        color=recipe["zone"]["color"], polygon_px=seed_polygon,
        source_id=result["source_ids"][seed_key],
    ))
    _action(log, f"Draw {recipe['zone']['name']} polygon on Camera 3",
            {"source_id": result["source_ids"][seed_key], "polygon_px": seed_polygon},
            "Stored the camera-specific floor-region trace as source pixel evidence.")
    _action(log, "Project Camera 3 polygon",
            {"source_id": result["source_ids"][seed_key], "zone_id": monitored_zone["id"],
             "projected_polygon_m": monitored_zone["polygon"]},
            "Projected the traced camera-floor polygon through its validated calibration.")
    view = geometry.create_zone_view(geometry.ZoneViewIn(
        zone_id=monitored_zone["id"], source_id=result["source_ids"][seed_key],
        outer_polygon_px=seed_polygon, detection_polygon_px=seed_polygon,
        membership_rule="point", threshold=0.5,
    ))
    result["zone_id"] = monitored_zone["id"]
    result["zone_name"] = recipe["zone"]["name"]
    result["zone_view_ids"] = [view["id"]]


def _stage_zone_extend(recipe: dict, result: dict, log: list) -> None:
    """The second camera's trace, projected and unioned into the same zone."""
    from ..routers import geometry, zones

    seed_key = recipe["zone"]["seed_camera_key"]
    views = list(result.get("zone_view_ids") or [])
    extensions = []
    for camera in recipe["cameras"]:
        if not camera.get("zone_view_px") or camera["key"] == seed_key:
            continue
        polygon = [{"x": p[0], "y": p[1]} for p in camera["zone_view_px"]]
        view = geometry.create_zone_view(geometry.ZoneViewIn(
            zone_id=result["zone_id"], source_id=result["source_ids"][camera["key"]],
            outer_polygon_px=polygon, detection_polygon_px=polygon,
            membership_rule="point", threshold=0.5,
        ))
        views.append(view["id"])
        _action(log, "Draw Aisle 04 polygon on Camera 4",
                {"source_id": result["source_ids"][camera["key"]], "polygon_px": polygon},
                "Stored the second camera-specific trace without inventing views for Cameras 1 or 2.")
        extended = geometry.extend_zone_from_view(view["id"])
        extensions.append({
            "camera_key": camera["key"],
            "zone_view_id": view["id"],
            "projected_contribution_m": extended["projected_contribution"],
        })
        _action(log, "Project Camera 4 polygon", extensions[-1],
                "Projected Camera 4 through its validated calibration into the same metric floor plane.")
    monitored_zone = zones.get_zone(result["zone_id"])
    _action(log, "Combine overlapping physical contributions",
            {"zone_view_ids": views, "extensions": extensions,
             "component_count": monitored_zone["component_count"]},
            "Only cameras 3 and 4 see Aisle 04; their projected floor footprints overlap into one polygon with revision provenance.")
    _action(log, f"Create canonical {recipe['zone']['name']}",
            {"zone_id": monitored_zone["id"], "geometry": monitored_zone["geometry"],
             "revision": monitored_zone["revision"]},
            "The canonical zone is metric geometry derived centrally from the two calibrated camera contributions.")
    result["zone_view_ids"] = views


def _stage_query(recipe: dict, result: dict, log: list) -> None:
    from ..routers import analyses

    query = analyses.create_analysis(analyses.AnalysisIn(
        name=recipe["query"]["name"], question=recipe["query"]["question"],
        subject="fused_entity", measures=["current_occupancy"],
        filters={"group_ids": [result["group_id"]], "zone_ids": [result["zone_id"]],
                 "entity_types": ["person"]}, created_by="agent", status="ready",
    ))
    _action(log, "Save the fused occupancy query", {"query_id": query["id"]},
            "Saved one canonical deterministic question; presentation is separate.")
    result["query_id"] = query["id"]


def _stage_alert(recipe: dict, result: dict, log: list) -> None:
    from ..routers import alerts

    rule = alerts.create_rule(alerts.RuleIn(
        name=recipe["alert"]["name"], kind="query_condition",
        params={"query_id": result["query_id"]},
        condition={"operator": recipe["alert"]["operator"], "value": recipe["alert"]["value"],
                   "for_seconds": 0, "window_s": 5},
        cooldown_s=recipe["alert"]["cooldown_s"], enabled=True,
    ))
    _action(log, "Create query-backed alert", {"alert_rule_id": rule["id"]},
            "The rule evaluates the saved fused occupancy query and is edge-triggered.")
    result["alert_rule_id"] = rule["id"]


def _stage_dashboard(recipe: dict, result: dict, log: list) -> None:
    from ..routers import dashboards

    dashboard = dashboards.create_dashboard(dashboards.DashboardIn(
        name=recipe["dashboard"]["name"],
        description="Guided demo view backed by the saved fused occupancy query.",
        created_by="agent",
    ))
    widget = dashboards.add_widget(dashboard["id"], dashboards.WidgetIn(
        query_id=result["query_id"], title=f"Fused people in {recipe['zone']['name']}",
        presentation="number",
    ))
    _action(log, "Generate query-backed dashboard",
            {"dashboard_id": dashboard["id"], "widget_id": widget["id"]},
            "The widget executes the same saved query used by the alert.")
    result["dashboard_id"] = dashboard["id"]


# Ordered stages that answer the demo's question. Each is applied by its own real
# operation so a walkthrough can show it happening at the step that explains it,
# and each carries the check that makes re-applying it a no-op.
REQUEST_STAGES = {
    "zone_seed": (_stage_zone_seed, lambda result: bool(result.get("zone_id"))),
    "zone_extend": (_stage_zone_extend,
                    lambda result: len(result.get("zone_view_ids") or []) >= 2),
    "query": (_stage_query, lambda result: bool(result.get("query_id"))),
    "alert": (_stage_alert, lambda result: bool(result.get("alert_rule_id"))),
    "dashboard": (_stage_dashboard, lambda result: bool(result.get("dashboard_id"))),
}
REQUEST_STAGE_ORDER = list(REQUEST_STAGES)


def _apply_request(path: Path, log: list, result: dict) -> tuple[list, dict]:
    """Apply every request stage in order, in one workspace context."""
    recipe = load_recipe()
    with db.using_database(str(path)):
        for stage in REQUEST_STAGE_ORDER:
            REQUEST_STAGES[stage][0](recipe, result, log)
    return log, result


def _setup_workspace(path: Path, session_id: str, base_url: str) -> tuple[list, dict]:
    """Full prepared demo workspace: space setup plus every request stage.

    The offline cache builder and the explore-only demo mode both need the whole
    configuration up front; the guided walkthrough applies the request stages as
    it explains them.
    """
    log, result = _setup_space(path, session_id, base_url)
    return _apply_request(path, log, result)


def create_session(base_url: str, mode: str = "guided") -> dict:
    if mode not in {"guided", "learn"}:
        raise HTTPException(422, "demo mode must be guided or learn")
    asset_root = resolve_asset_root()
    if asset_root is None:
        raise HTTPException(409, {"code": "demo_assets_missing", **asset_status()})
    logger.info("demo assets resolved", extra={"asset_kind": "nvidia_mv3dt"})
    metadata, _ = load_fixture()
    cache = load_derived_cache()
    session_id = uuid.uuid4().hex
    workspace_dir = (SESSION_ROOT / session_id).resolve()
    workspace_dir.mkdir(parents=True, exist_ok=False)
    workspace_path = workspace_dir / "storelens.db"
    db.init_db(str(workspace_path))
    try:
        # A guided session starts with camera and space setup only, so the
        # walkthrough can create the zone and its analyses at the step that
        # explains them. Explore-only sessions get the whole configuration.
        if mode == "guided":
            log, result = _setup_space(workspace_path, session_id, base_url.rstrip("/"))
        else:
            log, result = _setup_workspace(workspace_path, session_id, base_url.rstrip("/"))
        result["derived_replay"] = cache["metadata"]
    except Exception:
        shutil.rmtree(workspace_dir, ignore_errors=True)
        raise
    now = db.now()
    _normal_ex(
        "INSERT INTO demo_sessions (id,status,recipe_version,mode,workspace_path,asset_root,duration_s,"
        "action_log_json,result_json,created_at,updated_at,expires_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (session_id, "ready", load_recipe()["recipe_version"], mode, str(workspace_path),
         str(asset_root), float(cache["metadata"].get("duration_s", metadata["duration_s"])),
         json.dumps(log), json.dumps(result),
         now, now, now + 24 * 3600),
    )
    logger.info("demo session created", extra={"demo_session_id": session_id, "mode": mode})
    return get_session(session_id)


def apply_request_stage(session_id: str, stage: str) -> dict:
    """Apply one prepared request stage to an active guided session.

    Each stage runs the same real StoreLens operations the prepared workspace
    uses, so a walkthrough step reports work that actually happened. Applying a
    stage that already exists is a no-op, which keeps refreshes and retries safe.
    """
    if stage not in REQUEST_STAGES:
        raise HTTPException(422, f"stage must be one of {REQUEST_STAGE_ORDER}")
    row = _session_row(session_id)
    if row["status"] not in ACTIVE_STATES:
        raise HTTPException(409, "applying a demo request stage requires an active demo")
    if not _session_cache_current(row):
        raise HTTPException(409, "demo session belongs to an obsolete derived cache")
    apply_stage, already_applied = REQUEST_STAGES[stage]
    result = db.jload(row["result_json"], {})
    log = db.jload(row["action_log_json"], [])
    index = REQUEST_STAGE_ORDER.index(stage)
    for earlier in REQUEST_STAGE_ORDER[:index]:
        if not REQUEST_STAGES[earlier][1](result):
            raise HTTPException(409, f"demo request stage '{earlier}' must be applied first")
    if already_applied(result):
        return {"session_id": session_id, "stage": stage, "applied": False, "result": result}
    with db.using_database(row["workspace_path"]):
        apply_stage(load_recipe(), result, log)
    _normal_ex(
        "UPDATE demo_sessions SET action_log_json=?,result_json=?,updated_at=? WHERE id=?",
        (json.dumps(log), json.dumps(result), db.now(), session_id),
    )
    logger.info("demo request stage applied",
                extra={"demo_session_id": session_id, "stage": stage})
    return {"session_id": session_id, "stage": stage, "applied": True, "result": result}


def media_path(session_id: str, camera_key: str) -> Path:
    row = _session_row(session_id)
    recipe = load_recipe()
    if row["status"] not in ACTIVE_STATES:
        raise HTTPException(410 if row["status"] == "discarded" else 409,
                            "demo media requires an active current-version session")
    if row["recipe_version"] != recipe["recipe_version"]:
        raise HTTPException(409, "demo session belongs to an obsolete dataset recipe")
    if not _session_cache_current(row):
        raise HTTPException(409, "demo session belongs to an obsolete derived cache")
    allowed = {camera["key"] for camera in recipe["cameras"]}
    if camera_key not in allowed:
        raise HTTPException(404, "demo camera not found")
    root = Path(row["asset_root"] or "").resolve()
    path = (root / "videos" / f"{camera_key}.mp4").resolve()
    if root not in path.parents or not path.is_file():
        raise HTTPException(404, "demo media is not installed")
    return path


def camera_evidence(session_id: str, camera_key: str) -> dict:
    """Return allowlisted worker-local fixture output for the native video overlay.

    This data is camera-pixel evidence only. It contains no StoreLens zone,
    projection, multiview, query, or alert result. Those live in the separately
    versioned StoreLens-derived replay cache.
    """
    row = _session_row(session_id)
    recipe = load_recipe()
    if row["status"] not in ACTIVE_STATES:
        raise HTTPException(410 if row["status"] == "discarded" else 409,
                            "demo evidence requires an active current-version session")
    if row["recipe_version"] != recipe["recipe_version"]:
        raise HTTPException(409, "demo session belongs to an obsolete dataset recipe")
    if not _session_cache_current(row):
        raise HTTPException(409, "demo session belongs to an obsolete derived cache")
    allowed = {camera["key"] for camera in recipe["cameras"]}
    if camera_key not in allowed:
        raise HTTPException(404, "demo camera not found")
    metadata, records = load_fixture()
    return {
        "schema_version": metadata["schema_version"],
        "producer": metadata["producer"],
        "camera_key": camera_key,
        "fps": metadata["fps"],
        "frame_count": metadata["frame_count"],
        "frames": [record for record in records if record["source_key"] == camera_key],
    }


def replay_cache(session_id: str) -> dict:
    """Return the validated analytical cache used by synchronized demo playback."""
    row = _session_row(session_id)
    if row["status"] not in ACTIVE_STATES:
        raise HTTPException(410 if row["status"] == "discarded" else 409,
                            "demo replay cache requires an active session")
    if row["recipe_version"] != load_recipe()["recipe_version"]:
        raise HTTPException(409, "demo session belongs to an obsolete dataset recipe")
    if not _session_cache_current(row):
        raise HTTPException(409, "demo session belongs to an obsolete derived cache")
    return load_derived_cache()


def plan_path(session_id: str) -> Path:
    """Return only the allowlisted NVIDIA bird's-eye plan for an active demo."""
    row = _session_row(session_id)
    if row["status"] not in ACTIVE_STATES:
        raise HTTPException(410 if row["status"] == "discarded" else 409,
                            "demo plan requires an active current-version session")
    if row["recipe_version"] != load_recipe()["recipe_version"]:
        raise HTTPException(409, "demo session belongs to an obsolete dataset recipe")
    if not _session_cache_current(row):
        raise HTTPException(409, "demo session belongs to an obsolete derived cache")
    root = Path(row["asset_root"] or "").resolve()
    path = (root / "map.png").resolve()
    if root not in path.parents or not path.is_file():
        raise HTTPException(404, "demo bird's-eye plan is not installed")
    return path


def _validated_calibration(source_id: int) -> tuple[dict, dict] | None:
    """The imported rich calibration and its derived planar homography, if any."""
    rich = db.q1("SELECT * FROM camera_calibrations WHERE source_id=?", (source_id,))
    if not rich:
        return None
    validated = db.jload(rich["derived_homography_json"], {})
    if not validated.get("pixel_to_world"):
        return None
    return rich, validated


def _apply_validated_calibration(source: dict, rich: dict, validated: dict,
                                 comparison: dict | None = None) -> int:
    """Restore the imported NVIDIA matrix as the source's active floor calibration."""
    revision = int(source["calibration_revision"] or 0) + 1
    restored = {
        "H": validated["pixel_to_world"],
        "H_map_to_pixel": validated.get("world_to_pixel"),
        "frame_w": rich["frame_w"], "frame_h": rich["frame_h"],
        "provider": rich["provider"], "rich_calibration_id": rich["id"],
        "world_frame": db.jload(rich["world_frame_json"], {}),
        "units": "m", "ground_plane_z": rich["ground_plane_z"],
        "revision": revision,
    }
    if comparison is not None:
        restored["practice_comparison"] = comparison
    db.ex(
        "UPDATE sources SET calibration_json=?,calibration_revision=? WHERE id=?",
        (json.dumps(restored), revision, source["id"]),
    )
    return revision


def restore_practice_calibration(session_id: str, source_id: int) -> dict:
    """Compare a learned planar calibration, then restore validated demo geometry."""
    from . import homography

    row = _session_row(session_id)
    if row["status"] not in ACTIVE_STATES:
        raise HTTPException(409, "practice calibration requires an active demo")
    if not _session_cache_current(row):
        raise HTTPException(409, "demo session belongs to an obsolete derived cache")
    with db.using_database(row["workspace_path"]):
        source = db.q1("SELECT * FROM sources WHERE id=?", (source_id,))
        if not source:
            raise HTTPException(404, "demo source not found")
        metadata = db.jload(source["metadata_json"], {})
        camera_key = metadata.get("demo_fixture_source_key")
        if not camera_key:
            raise HTTPException(409, "source is not part of the guided replay")
        bundle = _validated_calibration(source_id)
        practice = db.jload(source["calibration_json"], {})
        practice_h = practice.get("H")
        if not practice_h or not bundle:
            raise HTTPException(409, "both practice and validated calibrations are required")
        rich, validated = bundle
        camera = next(item for item in load_recipe()["cameras"] if item["key"] == camera_key)
        points = camera.get("zone_view_px") or [
            [300, 300], [1200, 250], [1500, 850], [500, 900],
        ]
        practice_map = homography.project(practice_h, points)
        validated_map = homography.project(validated["pixel_to_world"], points)
        differences = [math.hypot(a[0] - b[0], a[1] - b[1])
                       for a, b in zip(practice_map, validated_map)]
        comparison = {
            "mean_difference_m": round(sum(differences) / len(differences), 4),
            "max_difference_m": round(max(differences), 4),
            "control_point_error_m": practice.get("error_m"),
            "sample_points": len(points),
        }
        _apply_validated_calibration(source, rich, validated, comparison)
    return {
        "source_id": source_id, "camera_key": camera_key, "comparison": comparison,
        "used_for_replay": "validated_nvidia_calibration",
        "explanation": "The practice homography was computed and compared. The validated matrix was restored for reliable replay.",
    }


def restore_practice_space(session_id: str) -> dict:
    """Compare a practice floor-plan trace, then restore the prepared demo space.

    The guided walkthrough can send a user through the real plan digitizer. Saving
    a real trace legitimately clears placements and floor calibrations, so this
    reinstates exactly the prepared recipe map, placements, and imported NVIDIA
    matrices. Both teaching paths therefore continue from one validated state and
    the committed replay keeps the projection it was derived with.
    """
    from ..routers import sources as sources_router

    row = _session_row(session_id)
    if row["status"] not in ACTIVE_STATES:
        raise HTTPException(409, "practice space restore requires an active demo")
    if not _session_cache_current(row):
        raise HTTPException(409, "demo session belongs to an obsolete derived cache")
    recipe = load_recipe()
    with db.using_database(row["workspace_path"]):
        current = db.q1("SELECT * FROM stores WHERE id=1")
        practice_trace = db.jload(current["map_json"], {}).get("blueprint_trace")
        comparison = {
            "practice_width_m": round(float(current["width_m"] or 0), 4),
            "practice_height_m": round(float(current["height_m"] or 0), 4),
            "restored_width_m": recipe["store"]["width_m"],
            "restored_height_m": recipe["store"]["height_m"],
            "practice_trace_present": bool(practice_trace),
        }
        db.ex(
            "UPDATE stores SET name=?,space_type=?,width_m=?,height_m=?,map_json=? WHERE id=1",
            (recipe["store"]["name"], recipe["store"]["space_type"], recipe["store"]["width_m"],
             recipe["store"]["height_m"], json.dumps(recipe["store"]["map"])),
        )
        restored: list[dict] = []
        for camera in recipe["cameras"]:
            source = db.q1(
                "SELECT * FROM sources WHERE json_extract(metadata_json,"
                "'$.demo_fixture_source_key')=?", (camera["key"],),
            )
            if not source:
                continue
            sources_router.set_placement(source["id"], sources_router.Placement(**camera["placement"]))
            bundle = _validated_calibration(source["id"])
            if bundle:
                rich, validated = bundle
                _apply_validated_calibration(source, rich, validated)
            restored.append({"source_id": source["id"], "camera_key": camera["key"],
                             "calibration_restored": bool(bundle)})
    return {
        "session_id": session_id, "comparison": comparison, "restored_sources": restored,
        "used_for_replay": "validated_nvidia_calibration",
        "explanation": "The practice trace was measured, then the prepared demo map, camera placements, and imported calibrations were restored.",
    }


def start(session_id: str) -> dict:
    row = _session_row(session_id)
    if row["status"] not in ACTIVE_STATES:
        raise HTTPException(409, f"cannot start a {row['status']} demo")
    if not _session_cache_current(row):
        raise HTTPException(409, "demo session belongs to an obsolete derived cache")
    clock = _clock(row)
    now = db.now()
    _normal_ex(
        "UPDATE demo_sessions SET status='running',playback_epoch=?,playback_position_s=?,"
        "playback_started_at=?,updated_at=? WHERE id=?",
        (clock["epoch"], clock["position_s"], now - clock["absolute_s"], now, session_id),
    )
    actions = db.jload(row["action_log_json"], [])
    if not any(item.get("name") == "Start synchronized derived replay" for item in actions):
        metadata = load_derived_cache()["metadata"]
        _action(actions, "Start synchronized derived replay",
                {"source_fps": metadata["source_fps"],
                 "derived_sample_rate_hz": metadata["sample_rate_hz"],
                 "runtime_gpu_required": False,
                 "payload_sha256": metadata["payload_sha256"]},
                "One lightweight master clock now drives native video, exact source evidence, and the offline StoreLens-derived cache.")
        _normal_ex("UPDATE demo_sessions SET action_log_json=?,updated_at=? WHERE id=?",
                   (json.dumps(actions), db.now(), session_id))
    logger.info("demo cached replay started", extra={"demo_session_id": session_id})
    return get_session(session_id)


def pause(session_id: str) -> dict:
    row = _session_row(session_id)
    if row["status"] != "running":
        raise HTTPException(409, "demo replay is not running")
    if not _session_cache_current(row):
        raise HTTPException(409, "demo session belongs to an obsolete derived cache")
    clock = _clock(row)
    _normal_ex(
        "UPDATE demo_sessions SET status='paused',playback_epoch=?,playback_position_s=?,"
        "playback_started_at=NULL,updated_at=? WHERE id=?",
        (clock["epoch"], clock["position_s"], db.now(), session_id),
    )
    return get_session(session_id)


async def _stop_replay(session_id: str, status: str) -> None:
    """Persist the lightweight master clock before changing lifecycle state."""
    row = _session_row(session_id)
    clock = _clock(row)
    _normal_ex(
        "UPDATE demo_sessions SET status=?,playback_epoch=?,playback_position_s=?,"
        "playback_started_at=NULL,updated_at=? WHERE id=?",
        (status, clock["epoch"], clock["position_s"], db.now(), session_id),
    )


async def discard(session_id: str) -> dict:
    row = _session_row(session_id)
    await _stop_replay(session_id, "discarded")
    workspace = Path(row["workspace_path"]).resolve().parent
    if SESSION_ROOT.resolve() not in workspace.parents:
        raise HTTPException(500, "refusing to remove an invalid demo workspace path")
    shutil.rmtree(workspace, ignore_errors=False)
    _normal_ex("UPDATE demo_sessions SET status='discarded',updated_at=? WHERE id=?", (db.now(), session_id))
    logger.info("demo session discarded", extra={"demo_session_id": session_id})
    return {"discarded": True, "session_id": session_id, "recoverable": False}


def cleanup_expired(now: float | None = None) -> int:
    cutoff = db.now() if now is None else now
    expired = _normal_rows(
        "SELECT * FROM demo_sessions WHERE expires_at IS NOT NULL AND expires_at<? "
        "AND status IN ('ready','running','paused')", (cutoff,))
    cleaned = 0
    for row in expired:
        workspace = Path(row["workspace_path"]).resolve().parent
        if SESSION_ROOT.resolve() in workspace.parents and workspace.exists():
            shutil.rmtree(workspace, ignore_errors=True)
        _normal_ex("UPDATE demo_sessions SET status='expired',updated_at=? WHERE id=?",
                   (cutoff, row["id"]))
        cleaned += 1
    return cleaned


async def promote(session_id: str, base_url: str, include_observations: bool = False) -> dict:
    """Promote only camera/space setup. Demo zones and analyses stay isolated."""
    row = _session_row(session_id)
    if row["status"] not in ACTIVE_STATES:
        raise HTTPException(409, "only an active demo can be promoted")
    if not _session_cache_current(row):
        raise HTTPException(409, "demo session belongs to an obsolete derived cache")
    await _stop_replay(session_id, "promoting")
    logger.info(
        "demo promotion started",
        extra={"demo_session_id": session_id, "include_observations": include_observations},
    )
    if include_observations:
        from ..routers.observations import (
            DetectionIn, DetectionSampleIn, ObservationBatch,
            detection_sample_batch, ingest_observations,
        )
        clock = _clock(row)
        _, raw_records = load_fixture()
        maximum_frame = math.floor(clock["position_s"] * load_recipe()["frame"]["fps"] + 1e-7)
        stride = max(1, round(load_recipe()["frame"]["fps"] /
                              load_derived_cache()["metadata"]["sample_rate_hz"]))
        source_ids = db.jload(row["result_json"], {})["source_ids"]
        observations = []
        for frame in raw_records:
            if frame["frame_index"] % stride or frame["frame_index"] > maximum_frame:
                continue
            runtime_sample_id = f"promote-e{clock['epoch']}:{frame['sample_id']}"
            sample = DetectionSampleIn(
                source_id=source_ids[frame["source_key"]], sample_id=runtime_sample_id,
                timestamp=float(row["created_at"]) + clock["epoch"] * float(row["duration_s"])
                + float(frame["video_time_s"]), frame_index=frame["frame_index"],
                entity_type="person", attributes={"producer_kind": "guided_demo_replay",
                                                    "playback_epoch": clock["epoch"]},
                detections=[DetectionIn(
                    entity_id=str(item["local_track_id"]), label="person",
                    confidence=item["confidence"], bbox_px=item["bbox_px"],
                    point_px=item["point_px"], identity_scope="source",
                    identity_model_version="yolo11n-bytetrack-fixture-v1",
                ) for item in frame["detections"]],
            )
            batch, _ = detection_sample_batch(sample)
            observations.extend(batch.observations)
        if observations:
            with db.using_database(row["workspace_path"]):
                result, _ = await ingest_observations(ObservationBatch(observations=observations))
            if result["rejected"]:
                raise HTTPException(500, "could not materialize opted-in demo observations")
    with db.using_database(row["workspace_path"]):
        demo_store = db.q1("SELECT * FROM stores WHERE id=1")
        demo_sources = db.q("SELECT * FROM sources ORDER BY id")
        demo_calibrations = db.q("SELECT * FROM camera_calibrations ORDER BY source_id")
        demo_groups = db.q("SELECT * FROM multiview_groups ORDER BY id")
        demo_events = db.q("SELECT * FROM events ORDER BY id") if include_observations else []
    from . import demo_media
    try:
        stream_base = demo_media.start(row["asset_root"])
    except Exception:
        _normal_ex("UPDATE demo_sessions SET status='paused',updated_at=? WHERE id=?", (db.now(), session_id))
        logger.exception("demo promotion failed", extra={"demo_session_id": session_id})
        raise
    con = db.connect(db.DB_PATH)
    source_map: dict[int, int] = {}
    try:
        con.execute("BEGIN IMMEDIATE")
        con.execute("UPDATE stores SET name=?,space_type=?,width_m=?,height_m=?,map_json=?,environment='setup' WHERE id=1",
                    (demo_store["name"], demo_store["space_type"], demo_store["width_m"],
                     demo_store["height_m"], demo_store["map_json"]))
        recipe_by_key = {camera["key"]: camera for camera in load_recipe()["cameras"]}
        for source in demo_sources:
            metadata = db.jload(source["metadata_json"], {})
            key = metadata["demo_fixture_source_key"]
            camera_index = list(recipe_by_key).index(key)
            url = f"{stream_base}/cam_{camera_index:02d}/stream.mjpg"
            cursor = con.execute(
                "INSERT INTO sources (name,kind,connection_mode,connection_management,connection_config_json,"
                "connection_revision,locator_json,capabilities_json,metadata_json,map_x,map_y,rotation_deg,fov_deg,"
                "calibration_json,calibration_revision,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (source["name"], "http", "agent_local", "storelens_managed",
                 json.dumps({"url": url, "auth_type": "none"}), 1, "{}", source["capabilities_json"],
                 json.dumps({"promoted_from_demo": session_id, "demo_fixture_source_key": key}),
                 source["map_x"], source["map_y"], source["rotation_deg"], source["fov_deg"],
                 source["calibration_json"], source["calibration_revision"], db.now()),
            )
            source_map[source["id"]] = cursor.lastrowid
        for calibration in demo_calibrations:
            columns = [key for key in calibration if key not in {"id", "source_id"}]
            con.execute(
                f"INSERT INTO camera_calibrations (source_id,{','.join(columns)}) VALUES "
                f"({','.join('?' for _ in range(len(columns) + 1))})",
                (source_map[calibration["source_id"]], *(calibration[key] for key in columns)),
            )
        for group in demo_groups:
            source_ids = [source_map[value] for value in db.jload(group["source_ids_json"], [])]
            con.execute(
                "INSERT INTO multiview_groups (name,source_ids_json,enabled,algorithm,algorithm_version,"
                "configuration_revision,time_tolerance_s,spatial_gate_m,track_age_s,topology_json,"
                "configuration_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (group["name"], json.dumps(source_ids), group["enabled"], group["algorithm"],
                 group["algorithm_version"], group["configuration_revision"], group["time_tolerance_s"],
                 group["spatial_gate_m"], group["track_age_s"], "{}", group["configuration_json"],
                 db.now(), db.now()),
            )
        promoted_observations = 0
        if include_observations:
            target_revision = con.execute("SELECT current_space_revision_id FROM stores WHERE id=1").fetchone()[0]
            for event in demo_events:
                columns = [key for key in event if key not in {"id", "source_id", "zone_id", "zone_view_id",
                                                               "projection_surface_id", "space_revision_id"}]
                attributes = db.jload(event["attributes"], {})
                attributes["promoted_from_demo"] = session_id
                detached = {"zone_assignment_method", "zone_revision", "zone_view_revision",
                            "surface_revision"}
                values = [json.dumps(attributes) if key == "attributes" else
                          None if key in detached else event[key] for key in columns]
                con.execute(
                    f"INSERT INTO events (source_id,zone_id,zone_view_id,projection_surface_id,space_revision_id,"
                    f"{','.join(columns)}) VALUES ({','.join('?' for _ in range(len(columns) + 5))})",
                    (source_map[event["source_id"]], None, None, None, target_revision, *values),
                )
                promoted_observations += 1
            for target_source_id in source_map.values():
                con.execute(
                    "UPDATE sources SET event_count=(SELECT COUNT(*) FROM events WHERE source_id=?),"
                    "last_observation_at=(SELECT MAX(ts) FROM events WHERE source_id=?),"
                    "last_ingestion_at=(SELECT MAX(created_at) FROM events WHERE source_id=?) WHERE id=?",
                    (target_source_id, target_source_id, target_source_id, target_source_id),
                )
        con.commit()
    except Exception:
        con.rollback()
        demo_media.stop()
        _normal_ex("UPDATE demo_sessions SET status='paused',updated_at=? WHERE id=?", (db.now(), session_id))
        logger.exception("demo promotion failed", extra={"demo_session_id": session_id})
        raise
    finally:
        con.close()
        # Promotion writes sources, calibrations and groups through a raw
        # connection, bypassing the db.ex configuration-cache hook.
        from . import config_cache
        config_cache.invalidate("demo_promotion")
    if include_observations and promoted_observations:
        from . import current_state, multiview as multiview_service
        with db.using_database(db.DB_PATH):
            current_state.rebuild_from_history()
            for sample in db.q("SELECT * FROM source_current_samples ORDER BY ts,source_id"):
                multiview_service.process_completed_sample({
                    "source_id": sample["source_id"], "entity_type": sample["entity_type"],
                    "sample_id": sample["sample_id"], "sample_key": sample["sample_key"],
                    "timestamp": sample["ts"], "expected_count": sample["expected_count"],
                    "marker_event_id": sample["marker_event_id"],
                })
    _normal_ex("UPDATE demo_sessions SET status='promoted',updated_at=? WHERE id=?", (db.now(), session_id))
    workspace = Path(row["workspace_path"]).resolve().parent
    if SESSION_ROOT.resolve() in workspace.parents:
        shutil.rmtree(workspace, ignore_errors=True)
    logger.info(
        "demo promotion succeeded",
        extra={"demo_session_id": session_id, "observations_promoted": promoted_observations},
    )
    return {"promoted": True, "source_id_map": source_map,
            "observations_promoted": promoted_observations,
            "excluded": [load_recipe()["zone"]["name"], "zone views", "saved query", "dashboard", "alert rule", "fired alerts"]}


def resume_active_sessions() -> int:
    """Validate persisted running sessions; their clocks need no worker task."""
    recovered = 0
    current_recipe = load_recipe()["recipe_version"]
    rows = _normal_rows("SELECT * FROM demo_sessions WHERE status='running' ORDER BY created_at")
    for row in rows:
        if (row["recipe_version"] != current_recipe
                or not Path(row["workspace_path"]).is_file()
                or not _session_cache_current(row)):
            _normal_ex(
                "UPDATE demo_sessions SET status='error',updated_at=? WHERE id=?",
                (db.now(), row["id"]),
            )
            continue
        recovered += 1
    return recovered


def resume_promoted_media() -> bool:
    """Restart the controlled stream process for an existing promoted sandbox."""
    from . import demo_media

    row = _normal_row(
        "SELECT * FROM demo_sessions WHERE status='promoted' ORDER BY updated_at DESC LIMIT 1"
    )
    if (not row or row["recipe_version"] != load_recipe()["recipe_version"]
            or not row["asset_root"] or resolve_asset_root(row["asset_root"]) is None):
        return False
    sources = _normal_rows("SELECT metadata_json FROM sources")
    if not any(db.jload(source["metadata_json"], {}).get("promoted_from_demo") == row["id"]
               for source in sources):
        return False
    demo_media.start(row["asset_root"])
    logger.info("promoted demo media resumed", extra={"demo_session_id": row["id"]})
    return True


async def shutdown() -> None:
    """Cached demo playback has no background processing task to stop."""
    return None
