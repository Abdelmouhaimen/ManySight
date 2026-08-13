"""Isolated guided-demo workspaces and deterministic observation replay."""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import shutil
import tempfile
import time
import uuid
from collections import defaultdict
from pathlib import Path

from fastapi import HTTPException

from .. import db

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "demo" / "fixtures" / "nvidia_mv3dt_yolo11n_bytetrack.jsonl"
RECIPE = ROOT / "demo" / "fixtures" / "nvidia_mv3dt_recipe.json"
UPSTREAM_ARCHIVE = (
    "https://github.com/NVIDIA/DeepStream/raw/refs/heads/main/"
    "src/apps/reference_apps/deepstream-tracker-3d-multi-view/assets/datasets.zip"
)
SESSION_ROOT = Path(tempfile.gettempdir()) / "storelens-demo-sessions"
ACTIVE_STATES = {"ready", "running", "paused"}
_tasks: dict[str, asyncio.Task] = {}
_controls: dict[str, dict] = {}
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


def resolve_asset_root(explicit: str | None = None) -> Path | None:
    candidates = [
        explicit,
        os.environ.get("STORELENS_DEMO_ASSET_DIR"),
        str(ROOT / "data" / "demo-assets" / "datasets" / "mtmc_4cam"),
        str(Path(tempfile.gettempdir()) / "storelens-demo-assets" / "datasets" / "mtmc_4cam"),
    ]
    for value in candidates:
        if not value:
            continue
        root = Path(value).expanduser().resolve()
        videos = root / "videos"
        if all((videos / f"Warehouse_Synthetic_Cam{i:03d}.mp4").is_file() for i in range(1, 5)):
            return root
    return None


def asset_status() -> dict:
    root = resolve_asset_root()
    return {
        "available": root is not None,
        "dataset": "NVIDIA DeepStream MV3DT mtmc_4cam synthetic warehouse sample",
        "download_url": UPSTREAM_ARCHIVE,
        "install_command": "python demo/fetch_nvidia_mv3dt.py",
        "environment_variable": "STORELENS_DEMO_ASSET_DIR",
        "redistributed_by_storelens": False,
    }


def _session_row(session_id: str) -> dict:
    row = _normal_row("SELECT * FROM demo_sessions WHERE id=?", (session_id,))
    if not row:
        raise HTTPException(404, "demo session not found")
    return row


def session_database(session_id: str) -> str | None:
    row = _normal_row("SELECT status,workspace_path FROM demo_sessions WHERE id=?", (session_id,))
    if not row or row["status"] not in ACTIVE_STATES:
        return None
    path = Path(row["workspace_path"]).resolve()
    root = SESSION_ROOT.resolve()
    if root not in path.parents or not path.is_file():
        return None
    return str(path)


def _public(row: dict) -> dict:
    control = _controls.get(row["id"])
    status = control.get("status") if control else row["status"]
    position = float(control.get("position_s", row["playback_position_s"])) if control else float(row["playback_position_s"])
    epoch = int(control.get("epoch", row["playback_epoch"])) if control else int(row["playback_epoch"])
    usage = {"database_bytes": 0, "observations": 0, "fused_observations": 0,
             "retained_epochs": int(row["retained_epochs"])}
    workspace = Path(row["workspace_path"])
    if workspace.is_file():
        usage["database_bytes"] = workspace.stat().st_size
        with db.using_database(str(workspace)):
            usage["observations"] = db.q1("SELECT COUNT(*) n FROM events")["n"]
            usage["fused_observations"] = db.q1("SELECT COUNT(*) n FROM fused_observations")["n"]
    return {
        "id": row["id"], "status": status, "mode": row["mode"],
        "recipe_version": row["recipe_version"], "playback_epoch": epoch,
        "playback_position_s": position, "duration_s": row["duration_s"],
        "action_log": db.jload(row["action_log_json"], []),
        "result": db.jload(row["result_json"], {}),
        "created_at": row["created_at"], "updated_at": row["updated_at"],
        "demo_workspace": True, "resource_usage": usage,
    }


def get_session(session_id: str) -> dict:
    return _public(_session_row(session_id))


def active_session() -> dict | None:
    row = _normal_row(
        "SELECT * FROM demo_sessions WHERE status IN ('ready','running','paused') ORDER BY created_at DESC LIMIT 1"
    )
    return _public(row) if row else None


def _action(log: list, name: str, result: dict, explanation: str) -> None:
    log.append({"name": name, "status": "completed", "result": result, "explanation": explanation})


def _setup_workspace(path: Path, session_id: str, base_url: str) -> tuple[list, dict]:
    from ..routers import alerts, analyses, calibrations, dashboards, geometry, multiview, sources, store, zones

    recipe = load_recipe()
    log: list[dict] = []
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
        first = recipe["cameras"][0]
        projected_zone = zones.create_zone(zones.ZoneIn(
            name=recipe["zone"]["name"], ztype=recipe["zone"]["ztype"],
            color=recipe["zone"]["color"], polygon_px=[{"x": p[0], "y": p[1]}
                                                       for p in first["zone_projection_px"]],
            source_id=source_ids[first["key"]],
        ))
        _action(log, "Project the Aisle 04 camera polygon",
                {"source_id": source_ids[first["key"]], "zone_id": projected_zone["id"],
                 "projected_polygon_m": projected_zone["polygon"]},
                "StoreLens projected predetermined camera pixels through the imported floor calibration.")
        views = []
        for camera in recipe["cameras"]:
            polygon = [{"x": p[0], "y": p[1]} for p in camera["zone_view_px"]]
            view = geometry.create_zone_view(geometry.ZoneViewIn(
                zone_id=projected_zone["id"], source_id=source_ids[camera["key"]],
                outer_polygon_px=polygon, detection_polygon_px=polygon,
                membership_rule="point", threshold=0.5,
            ))
            views.append(view["id"])
        _action(log, "Create camera-specific Aisle 04 views", {"zone_view_ids": views},
                "The views are source pixel evidence; the canonical zone remains metric map geometry.")
        group = multiview.create_group(multiview.MultiviewGroupIn(
            name=recipe["multiview"]["name"], source_ids=list(source_ids.values()),
            time_tolerance_s=recipe["multiview"]["time_tolerance_s"],
            spatial_gate_m=recipe["multiview"]["spatial_gate_m"],
            track_age_s=recipe["multiview"]["track_age_s"],
            configuration={"producer": "fixture_replay", "appearance_reid": False},
        ))
        _action(log, "Create calibrated multiview group", {"group_id": group["id"]},
                "Enabled StoreLens-owned geometry/time association for anonymous source-local tracks.")
        query = analyses.create_analysis(analyses.AnalysisIn(
            name=recipe["query"]["name"], question=recipe["query"]["question"],
            subject="fused_entity", measures=["current_occupancy"],
            filters={"group_ids": [group["id"]], "zone_ids": [projected_zone["id"]],
                     "entity_types": ["person"]}, created_by="agent", status="ready",
        ))
        _action(log, "Save the fused occupancy query", {"query_id": query["id"]},
                "Saved one canonical deterministic question; presentation is separate.")
        dashboard = dashboards.create_dashboard(dashboards.DashboardIn(
            name=recipe["dashboard"]["name"],
            description="Guided demo view backed by the saved fused occupancy query.",
            created_by="agent",
        ))
        widget = dashboards.add_widget(dashboard["id"], dashboards.WidgetIn(
            query_id=query["id"], title="Fused people in Aisle 04", presentation="number",
        ))
        _action(log, "Generate query-backed dashboard", {"dashboard_id": dashboard["id"],
                                                          "widget_id": widget["id"]},
                "The widget executes the same saved query used by the alert.")
        rule = alerts.create_rule(alerts.RuleIn(
            name=recipe["alert"]["name"], kind="query_condition", params={"query_id": query["id"]},
            condition={"operator": recipe["alert"]["operator"], "value": recipe["alert"]["value"],
                       "for_seconds": 0, "window_s": 5},
            cooldown_s=recipe["alert"]["cooldown_s"], enabled=True,
        ))
        _action(log, "Create query-backed alert", {"alert_rule_id": rule["id"]},
                "The rule evaluates the saved fused occupancy query and is edge-triggered.")
    return log, {"source_ids": source_ids, "zone_id": projected_zone["id"],
                 "group_id": group["id"], "query_id": query["id"],
                 "dashboard_id": dashboard["id"], "alert_rule_id": rule["id"]}


def create_session(base_url: str, mode: str = "guided") -> dict:
    if mode not in {"guided", "learn"}:
        raise HTTPException(422, "demo mode must be guided or learn")
    asset_root = resolve_asset_root()
    if asset_root is None:
        raise HTTPException(409, {"code": "demo_assets_missing", **asset_status()})
    logger.info("demo assets resolved", extra={"asset_kind": "nvidia_mv3dt"})
    metadata, _ = load_fixture()
    session_id = uuid.uuid4().hex
    workspace_dir = (SESSION_ROOT / session_id).resolve()
    workspace_dir.mkdir(parents=True, exist_ok=False)
    workspace_path = workspace_dir / "storelens.db"
    db.init_db(str(workspace_path))
    try:
        log, result = _setup_workspace(workspace_path, session_id, base_url.rstrip("/"))
    except Exception:
        shutil.rmtree(workspace_dir, ignore_errors=True)
        raise
    now = db.now()
    _normal_ex(
        "INSERT INTO demo_sessions (id,status,recipe_version,mode,workspace_path,asset_root,duration_s,"
        "action_log_json,result_json,created_at,updated_at,expires_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (session_id, "ready", load_recipe()["recipe_version"], mode, str(workspace_path),
         str(asset_root), float(metadata["duration_s"]), json.dumps(log), json.dumps(result),
         now, now, now + 24 * 3600),
    )
    logger.info("demo session created", extra={"demo_session_id": session_id, "mode": mode})
    return get_session(session_id)


def media_path(session_id: str, camera_key: str) -> Path:
    row = _session_row(session_id)
    if row["status"] == "discarded":
        raise HTTPException(410, "demo session was discarded")
    recipe = load_recipe()
    allowed = {camera["key"] for camera in recipe["cameras"]}
    if camera_key not in allowed:
        raise HTTPException(404, "demo camera not found")
    root = Path(row["asset_root"] or "").resolve()
    path = (root / "videos" / f"{camera_key}.mp4").resolve()
    if root not in path.parents or not path.is_file():
        raise HTTPException(404, "demo media is not installed")
    return path


def restore_practice_calibration(session_id: str, source_id: int) -> dict:
    """Compare a learned planar calibration, then restore validated demo geometry."""
    from . import homography

    row = _session_row(session_id)
    if row["status"] not in ACTIVE_STATES:
        raise HTTPException(409, "practice calibration requires an active demo")
    with db.using_database(row["workspace_path"]):
        source = db.q1("SELECT * FROM sources WHERE id=?", (source_id,))
        if not source:
            raise HTTPException(404, "demo source not found")
        metadata = db.jload(source["metadata_json"], {})
        camera_key = metadata.get("demo_fixture_source_key")
        if not camera_key:
            raise HTTPException(409, "source is not part of the guided replay")
        rich = db.q1("SELECT * FROM camera_calibrations WHERE source_id=?", (source_id,))
        practice = db.jload(source["calibration_json"], {})
        practice_h = practice.get("H")
        validated = db.jload(rich["derived_homography_json"], {}) if rich else {}
        validated_h = validated.get("pixel_to_world")
        if not practice_h or not validated_h:
            raise HTTPException(409, "both practice and validated calibrations are required")
        camera = next(item for item in load_recipe()["cameras"] if item["key"] == camera_key)
        points = camera["zone_view_px"]
        practice_map = homography.project(practice_h, points)
        validated_map = homography.project(validated_h, points)
        differences = [math.hypot(a[0] - b[0], a[1] - b[1])
                       for a, b in zip(practice_map, validated_map)]
        comparison = {
            "mean_difference_m": round(sum(differences) / len(differences), 4),
            "max_difference_m": round(max(differences), 4),
            "control_point_error_m": practice.get("error_m"),
            "sample_points": len(points),
        }
        revision = int(source["calibration_revision"] or 0) + 1
        restored = {
            "H": validated_h,
            "H_map_to_pixel": validated.get("world_to_pixel"),
            "frame_w": rich["frame_w"], "frame_h": rich["frame_h"],
            "provider": rich["provider"], "rich_calibration_id": rich["id"],
            "world_frame": db.jload(rich["world_frame_json"], {}),
            "units": "m", "ground_plane_z": rich["ground_plane_z"],
            "revision": revision, "practice_comparison": comparison,
        }
        db.ex(
            "UPDATE sources SET calibration_json=?,calibration_revision=? WHERE id=?",
            (json.dumps(restored), revision, source_id),
        )
    return {
        "source_id": source_id, "camera_key": camera_key, "comparison": comparison,
        "used_for_replay": "validated_nvidia_calibration",
        "explanation": "The practice homography was computed and compared. The validated matrix was restored for reliable replay.",
    }


async def _replay(session_id: str) -> None:
    from ..routers.observations import ObservationBatch, ObservationIn, submit_observations

    metadata, records = load_fixture()
    by_time: dict[float, list[dict]] = defaultdict(list)
    for record in records:
        by_time[float(record["video_time_s"])].append(record)
    timeline = sorted(by_time)
    row = _session_row(session_id)
    result = db.jload(row["result_json"], {})
    source_ids = result["source_ids"]
    control = _controls[session_id]
    try:
        while not control["stop"]:
            if control["status"] == "paused":
                control["last_tick"] = db.now()
                await asyncio.sleep(0.1)
                continue
            if db.now() < control.get("hold_until", 0):
                control["last_tick"] = db.now()
                await asyncio.sleep(0.05)
                continue
            epoch = control["epoch"]
            epoch_started = control["epoch_started"]
            tick = db.now()
            position = control["position_s"] + max(0.0, tick - control["last_tick"])
            control["last_tick"] = tick
            control["position_s"] = min(position, control["duration_s"])
            # Process one synchronized source timestamp per turn. Derivation time does
            # not advance the media clock, so the browser cannot outrun evidence and
            # API/SSE requests are never starved by an unbounded catch-up burst.
            due = [stamp for stamp in timeline if control["next_time"] <= stamp <= position][:1]
            for stamp in due:
                observations = []
                sample_ts = epoch_started + stamp
                for frame in sorted(by_time[stamp], key=lambda value: value["source_key"]):
                    source_key = frame["source_key"]
                    source_id = source_ids[source_key]
                    sample_id = f"demo-e{epoch}-f{frame['frame_index']}-{source_key}"
                    for index, detection in enumerate(frame["detections"]):
                        local_id = detection["local_track_id"] or f"untracked-{index}"
                        observations.append(ObservationIn(
                            schema_version=2,
                            observation_id=f"demo:{session_id}:{epoch}:{source_key}:{frame['frame_index']}:d:{index}",
                            sample_id=sample_id, kind="detection", timestamp=sample_ts,
                            source_id=source_id, confidence=detection["confidence"],
                            entity_id=f"e{epoch}:{source_key}:{local_id}", entity_type="person",
                            label="person", identity_scope="source",
                            identity_model_version="yolo11n-bytetrack-fixture-v1",
                            geometry={"bbox_px": detection["bbox_px"], "point_px": detection["point_px"]},
                            attributes={"producer_kind": "replay", "fixture_schema_version": 1,
                                        "playback_epoch": epoch, "source_frame_index": frame["frame_index"]},
                        ))
                    observations.append(ObservationIn(
                        schema_version=2,
                        observation_id=f"demo:{session_id}:{epoch}:{source_key}:{frame['frame_index']}:marker",
                        sample_id=sample_id, kind="measurement", timestamp=sample_ts,
                        source_id=source_id, name="detection_frame_count", label="person",
                        value=len(frame["detections"]), value_kind="gauge", unit="detections",
                        attributes={"producer_kind": "replay", "fixture_schema_version": 1,
                                    "playback_epoch": epoch, "source_frame_index": frame["frame_index"]},
                    ))
                with db.using_database(row["workspace_path"]):
                    await submit_observations(ObservationBatch(observations=observations))
                    from . import alert_engine
                    zone_names = {item["id"]: item["name"] for item in db.q("SELECT id,name FROM zones")}
                    fired = alert_engine.evaluate_ongoing(sample_ts, zone_names)
                    if fired:
                        logger.info(
                            "demo query alert fired",
                            extra={"demo_session_id": session_id, "alert_count": len(fired)},
                        )
                        if not control.get("acceptance_alert_seen"):
                            # Keep the real threshold state visible long enough for
                            # polling UIs to render it, then continue normal playback.
                            control["acceptance_alert_seen"] = True
                            control["hold_until"] = db.now() + 2.0
                control["next_time"] = stamp + 1e-6
                control["last_tick"] = db.now()
            if position >= control["duration_s"]:
                _reset_epoch_state(row["workspace_path"], db.now())
                control["epoch"] += 1
                logger.info(
                    "demo replay epoch incremented",
                    extra={"demo_session_id": session_id, "playback_epoch": control["epoch"]},
                )
                control["epoch_started"] = epoch_started + control["duration_s"]
                control["position_s"] = 0.0
                control["next_time"] = 0.0
                control["last_tick"] = db.now()
                _prune_epochs(row["workspace_path"], session_id, control["epoch"], row["retained_epochs"])
            if db.now() - control["last_persisted"] >= 1:
                _normal_ex(
                    "UPDATE demo_sessions SET status=?,playback_epoch=?,playback_position_s=?,"
                    "playback_started_at=?,updated_at=? WHERE id=?",
                    (control["status"], control["epoch"], control["position_s"],
                     control["epoch_started"], db.now(), session_id),
                )
                control["last_persisted"] = db.now()
            await asyncio.sleep(0.05)
    finally:
        _tasks.pop(session_id, None)
        if control.get("stop"):
            _controls.pop(session_id, None)


def _prune_epochs(workspace_path: str, session_id: str, current_epoch: int, retained: int) -> None:
    oldest = max(0, current_epoch - int(retained) + 1)
    with db.using_database(workspace_path):
        rows = db.q("SELECT id,observation_id FROM events WHERE observation_id LIKE ?", (f"demo:{session_id}:%",))
        remove = []
        for row in rows:
            try:
                epoch = int(row["observation_id"].split(":", 3)[2])
            except (ValueError, IndexError):
                continue
            if epoch < oldest:
                remove.append(row["id"])
        for start in range(0, len(remove), 500):
            chunk = remove[start:start + 500]
            db.ex(f"DELETE FROM events WHERE id IN ({','.join('?' for _ in chunk)})", chunk)
        earliest = db.q1(
            "SELECT MIN(ts) ts FROM events WHERE observation_id LIKE ?",
            (f"demo:{session_id}:%",),
        )["ts"]
        if earliest is not None:
            db.ex("DELETE FROM fused_observations WHERE ts<?", (earliest,))
            db.ex("DELETE FROM zone_occupancy_observations WHERE ts<?", (earliest,))
            db.ex("DELETE FROM alerts WHERE ts<?", (earliest,))
            ended = [item["id"] for item in db.q(
                "SELECT id FROM fused_entities WHERE ended_at IS NOT NULL AND last_seen_at<?", (earliest,)
            )]
            for fused_id in ended:
                db.ex("DELETE FROM fused_entity_members WHERE fused_entity_id=?", (fused_id,))
                db.ex("DELETE FROM fused_entities WHERE id=?", (fused_id,))


def _reset_epoch_state(workspace_path: str, at_time: float) -> None:
    """Prevent identities or instantaneous state from crossing a media rewind."""
    with db.using_database(workspace_path):
        db.ex("UPDATE fused_entities SET ended_at=? WHERE ended_at IS NULL", (at_time,))
        for table in ("source_current_entities", "source_current_samples",
                      "fused_current_entities", "zone_current_occupancy"):
            db.ex(f"DELETE FROM {table}")


def start(session_id: str) -> dict:
    row = _session_row(session_id)
    if row["status"] not in ACTIVE_STATES:
        raise HTTPException(409, f"cannot start a {row['status']} demo")
    task = _tasks.get(session_id)
    if task and not task.done():
        _controls[session_id]["status"] = "running"
        _controls[session_id]["last_tick"] = db.now()
        return get_session(session_id)
    position = float(row["playback_position_s"] or 0)
    now = db.now()
    _controls[session_id] = {
        "status": "running", "stop": False, "epoch": int(row["playback_epoch"] or 0),
        "position_s": position, "duration_s": float(row["duration_s"]),
        "epoch_started": float(row["playback_started_at"] or (now - position)),
        "next_time": position, "last_tick": now,
        "acceptance_alert_seen": False, "hold_until": 0.0,
        "last_persisted": 0.0,
    }
    _normal_ex("UPDATE demo_sessions SET status='running',playback_started_at=?,updated_at=? WHERE id=?",
               (_controls[session_id]["epoch_started"], now, session_id))
    actions = db.jload(row["action_log_json"], [])
    if not any(item.get("name") == "Start deterministic observation replay" for item in actions):
        _action(actions, "Start deterministic observation replay",
                {"producer_kind": "replay", "fixture_schema_version": 1,
                 "runtime_gpu_required": False},
                "The replay controller submits progressive schema-v2 source samples through the normal ingestion boundary.")
        _normal_ex("UPDATE demo_sessions SET action_log_json=?,updated_at=? WHERE id=?",
                   (json.dumps(actions), db.now(), session_id))
    _tasks[session_id] = asyncio.create_task(_replay(session_id))
    logger.info("demo replay started", extra={"demo_session_id": session_id})
    return get_session(session_id)


def pause(session_id: str) -> dict:
    control = _controls.get(session_id)
    if not control:
        raise HTTPException(409, "demo replay is not running")
    control["status"] = "paused"
    control["last_tick"] = db.now()
    _normal_ex("UPDATE demo_sessions SET status='paused',playback_position_s=?,updated_at=? WHERE id=?",
               (control["position_s"], db.now(), session_id))
    return get_session(session_id)


def restart(session_id: str) -> dict:
    row = _session_row(session_id)
    with db.using_database(row["workspace_path"]):
        from ..routers.workspace import _clear_observations
        con = db.connect()
        try:
            con.execute("BEGIN IMMEDIATE"); _clear_observations(con); con.commit()
        finally:
            con.close()
    control = _controls.get(session_id)
    if control:
        control.update({"epoch": 0, "position_s": 0.0, "epoch_started": db.now(),
                        "next_time": 0.0, "last_tick": db.now(), "status": "running",
                        "acceptance_alert_seen": False, "hold_until": 0.0})
    else:
        _normal_ex("UPDATE demo_sessions SET playback_epoch=0,playback_position_s=0,status='ready' WHERE id=?",
                   (session_id,))
        return start(session_id)
    return get_session(session_id)


async def _stop_replay(session_id: str, status: str) -> None:
    """Stop one replay task and wait until it releases the temporary database."""
    control = _controls.get(session_id)
    if control:
        control["status"] = status
        control["stop"] = True
    task = _tasks.get(session_id)
    if task and task is not asyncio.current_task():
        await asyncio.gather(task, return_exceptions=True)


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
        control = _controls.get(row["id"])
        if control:
            control["status"] = "expired"; control["stop"] = True
        workspace = Path(row["workspace_path"]).resolve().parent
        if SESSION_ROOT.resolve() in workspace.parents and workspace.exists():
            shutil.rmtree(workspace, ignore_errors=True)
        _normal_ex("UPDATE demo_sessions SET status='expired',updated_at=? WHERE id=?",
                   (cutoff, row["id"]))
        cleaned += 1
    return cleaned


async def promote(session_id: str, base_url: str, include_observations: bool = False) -> dict:
    """Promote only camera/space setup. Demo analyses and Aisle 04 stay isolated."""
    row = _session_row(session_id)
    if row["status"] not in ACTIVE_STATES:
        raise HTTPException(409, "only an active demo can be promoted")
    await _stop_replay(session_id, "promoting")
    logger.info(
        "demo promotion started",
        extra={"demo_session_id": session_id, "include_observations": include_observations},
    )
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
            "excluded": ["Aisle 04", "zone views", "saved query", "dashboard", "alert rule", "fired alerts"]}


def resume_active_sessions() -> int:
    """Recover persisted running sessions after a local server restart."""
    recovered = 0
    rows = _normal_rows("SELECT * FROM demo_sessions WHERE status='running' ORDER BY created_at")
    for row in rows:
        if not Path(row["workspace_path"]).is_file():
            _normal_ex(
                "UPDATE demo_sessions SET status='error',updated_at=? WHERE id=?",
                (db.now(), row["id"]),
            )
            continue
        start(row["id"])
        recovered += 1
    return recovered


def resume_promoted_media() -> bool:
    """Restart the controlled stream process for an existing promoted sandbox."""
    row = _normal_row(
        "SELECT * FROM demo_sessions WHERE status='promoted' ORDER BY updated_at DESC LIMIT 1"
    )
    if not row or not row["asset_root"] or resolve_asset_root(row["asset_root"]) is None:
        return False
    sources = _normal_rows("SELECT metadata_json FROM sources")
    if not any(db.jload(source["metadata_json"], {}).get("promoted_from_demo") == row["id"]
               for source in sources):
        return False
    demo_media.start(row["asset_root"])
    logger.info("promoted demo media resumed", extra={"demo_session_id": row["id"]})
    return True


async def shutdown() -> None:
    for control in _controls.values():
        control["stop"] = True
    tasks = list(_tasks.values())
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
