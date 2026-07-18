"""Logical observation sources and their map/projection configuration.

StoreLens never opens a source. Camera access belongs to the agent-authored worker
running where the device is reachable. The hosted platform stores only non-secret
local locator hints, capabilities, geometry, and observation health.
"""
import json
import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .. import db
from ..services import homography
from .jobs import serialize_worker

router = APIRouter(tags=["sources"])

KINDS = {"rtsp", "webrtc", "http", "webcam", "file", "sensor", "custom"}
CONNECTION_MODES = {"agent_local", "edge_gateway"}
VIDEO_KINDS = {"rtsp", "webrtc", "http", "webcam", "file"}
FORBIDDEN_LOCATOR_KEYS = {
    "url", "uri", "username", "password", "token", "api_key", "apikey",
    "secret", "credential", "credentials", "connection_string",
}


class SourceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    kind: str = "webcam"
    connection_mode: str = "agent_local"
    locator: dict = Field(default_factory=dict)
    capabilities: list[str] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)


class SourcePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    kind: str | None = None
    connection_mode: str | None = None
    locator: dict | None = None
    capabilities: list[str] | None = None
    metadata: dict | None = None


class Placement(BaseModel):
    x: float
    y: float
    rotation_deg: float = 0
    fov_deg: float = 70


class CalibrationIn(BaseModel):
    points: list[dict]  # [{"px": {x,y}, "map": {x,y}}, ...] >= 4
    frame_w: int | None = None
    frame_h: int | None = None


class ProjectIn(BaseModel):
    points: list[dict]  # [{x,y}] pixel coords
    surface_id: int | None = None  # null = saved floor calibration


class UnprojectIn(BaseModel):
    points: list[dict]  # [{x,y}] map metres
    surface_id: int | None = None


def _validate_source(kind: str, connection_mode: str, locator: dict):
    if kind not in KINDS:
        raise HTTPException(422, f"kind must be one of {sorted(KINDS)}")
    if connection_mode not in CONNECTION_MODES:
        raise HTTPException(422, f"connection_mode must be one of {sorted(CONNECTION_MODES)}")

    def walk(value, path="locator"):
        if isinstance(value, dict):
            for key, item in value.items():
                normalized = str(key).lower().replace("-", "_")
                if normalized in FORBIDDEN_LOCATOR_KEYS:
                    raise HTTPException(
                        422,
                        f"{path}.{key} may contain camera access or credentials; "
                        "store them on the worker device and use local_secret_ref instead",
                    )
                walk(item, f"{path}.{key}")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, f"{path}[{index}]")
        elif isinstance(value, str) and re.match(r"^(rtsp|rtsps|https?)://", value, re.I):
            raise HTTPException(
                422,
                f"{path} must not contain a network camera URL; use a local_secret_ref",
            )

    walk(locator)


def _runtime_by_source() -> dict[int, dict]:
    runtime: dict[int, dict] = {}
    for job in db.q("SELECT id, name, source_ids, status FROM jobs ORDER BY created_at DESC"):
        worker = db.q1(
            "SELECT * FROM worker_instances WHERE job_id=? ORDER BY created_at DESC LIMIT 1",
            (job["id"],),
        )
        summary = {
            "job_id": job["id"],
            "job_name": job["name"],
            "job_status": job["status"],
            "worker": serialize_worker(worker) if worker else None,
        }
        for source_id in db.jload(job["source_ids"], []):
            try:
                runtime.setdefault(int(source_id), summary)
            except (TypeError, ValueError):
                continue
    return runtime


def serialize(row: dict, runtime: dict | None = None) -> dict:
    cal = db.jload(row.get("calibration_json"), None) if row.get("calibration_json") else None
    last_ingestion = row.get("last_ingestion_at")
    age = max(0.0, db.now() - last_ingestion) if last_ingestion else None
    observation_status = (
        "never" if age is None else
        "active" if age <= 30 else
        "recent" if age <= 300 else
        "stale"
    )
    return {
        "id": row["id"],
        "name": row["name"],
        "kind": row["kind"],
        "connection_mode": row.get("connection_mode") or "agent_local",
        "locator": db.jload(row.get("locator_json"), {}),
        "capabilities": db.jload(row.get("capabilities_json"), []),
        "metadata": db.jload(row.get("metadata_json"), {}),
        "observation_status": observation_status,
        "last_observation_at": row.get("last_observation_at"),
        "last_ingestion_at": last_ingestion,
        "observation_age_s": age,
        "event_count": int(row.get("event_count") or 0),
        "placement": (
            {"x": row["map_x"], "y": row["map_y"], "rotation_deg": row["rotation_deg"], "fov_deg": row["fov_deg"]}
            if row["map_x"] is not None else None
        ),
        "calibrated": bool(cal and cal.get("H")),
        "calibration": cal,
        "calibration_revision": row.get("calibration_revision", 0),
        "latest_runtime": runtime,
        "created_at": row["created_at"],
    }


def _get(source_id: int) -> dict:
    row = db.q1("SELECT * FROM sources WHERE id=?", (source_id,))
    if not row:
        raise HTTPException(404, "source not found")
    return row


@router.get("/sources")
def list_sources():
    runtime = _runtime_by_source()
    return [serialize(r, runtime.get(r["id"])) for r in db.q("SELECT * FROM sources ORDER BY id")]


@router.post("/sources", status_code=201)
def create_source(body: SourceIn):
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "name is required")
    _validate_source(body.kind, body.connection_mode, body.locator)
    capabilities = body.capabilities or (["video"] if body.kind in VIDEO_KINDS else [])
    sid = db.ex(
        "INSERT INTO sources (name,kind,connection_mode,locator_json,capabilities_json,metadata_json,created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (name, body.kind, body.connection_mode, json.dumps(body.locator),
         json.dumps(capabilities), json.dumps(body.metadata), db.now()),
    )
    return serialize(_get(sid))


@router.get("/sources/{source_id}")
def get_source(source_id: int):
    return serialize(_get(source_id), _runtime_by_source().get(source_id))


@router.put("/sources/{source_id}")
def update_source(source_id: int, body: SourcePatch):
    row = _get(source_id)
    kind = body.kind or row["kind"]
    mode = body.connection_mode or row.get("connection_mode") or "agent_local"
    locator = body.locator if body.locator is not None else db.jload(row.get("locator_json"), {})
    _validate_source(kind, mode, locator)
    fields = {
        "name": body.name.strip() if body.name is not None else None,
        "kind": body.kind,
        "connection_mode": body.connection_mode,
        "locator_json": json.dumps(body.locator) if body.locator is not None else None,
        "capabilities_json": json.dumps(body.capabilities) if body.capabilities is not None else None,
        "metadata_json": json.dumps(body.metadata) if body.metadata is not None else None,
    }
    sets = {k: v for k, v in fields.items() if v is not None}
    if body.name is not None and not fields["name"]:
        raise HTTPException(422, "name is required")
    if sets:
        db.ex(f"UPDATE sources SET {', '.join(f'{k}=?' for k in sets)} WHERE id=?", (*sets.values(), source_id))
    return serialize(_get(source_id))


@router.delete("/sources/{source_id}")
def delete_source(source_id: int):
    _get(source_id)
    db.ex("DELETE FROM zone_views WHERE source_id=?", (source_id,))
    db.ex("DELETE FROM projection_surfaces WHERE source_id=?", (source_id,))
    db.ex("DELETE FROM sources WHERE id=?", (source_id,))
    return {"deleted": source_id}


@router.put("/sources/{source_id}/placement")
def set_placement(source_id: int, body: Placement):
    _get(source_id)
    db.ex(
        "UPDATE sources SET map_x=?, map_y=?, rotation_deg=?, fov_deg=? WHERE id=?",
        (body.x, body.y, body.rotation_deg, body.fov_deg, source_id),
    )
    return serialize(_get(source_id))


@router.delete("/sources/{source_id}/placement")
def clear_placement(source_id: int):
    _get(source_id)
    db.ex("UPDATE sources SET map_x=NULL, map_y=NULL WHERE id=?", (source_id,))
    return serialize(_get(source_id))


@router.put("/sources/{source_id}/calibration")
def set_calibration(source_id: int, body: CalibrationIn):
    row = _get(source_id)
    try:
        H, err = homography.compute_homography(body.points)
    except ValueError as e:
        raise HTTPException(422, str(e))
    import json
    revision = int(row.get("calibration_revision") or 0) + 1
    cal = {"points": body.points, "H": H, "error_m": err, "frame_w": body.frame_w,
           "frame_h": body.frame_h, "revision": revision, "plane": "floor"}
    db.ex("UPDATE sources SET calibration_json=?, calibration_revision=? WHERE id=?",
          (json.dumps(cal), revision, source_id))
    return {"H": H, "error_m": err, "points": len(body.points), "revision": revision,
            "plane": "floor"}


@router.delete("/sources/{source_id}/calibration")
def clear_calibration(source_id: int):
    row = _get(source_id)
    revision = int(row.get("calibration_revision") or 0) + 1
    db.ex("UPDATE sources SET calibration_json=NULL, calibration_revision=? WHERE id=?",
          (revision, source_id))
    return {"cleared": True, "revision": revision}


def _projection(source_id: int, surface_id: int | None) -> tuple[list, str, int]:
    row = _get(source_id)
    if surface_id is not None:
        surface = db.q1("SELECT * FROM projection_surfaces WHERE id=?", (surface_id,))
        if not surface:
            raise HTTPException(404, "projection surface not found")
        if surface["source_id"] != source_id:
            raise HTTPException(422, "projection surface belongs to a different source")
        return db.jload(surface["homography_json"], None), surface["name"], surface["revision"]
    cal = db.jload(row.get("calibration_json"), None)
    if not cal or not cal.get("H"):
        raise HTTPException(409, "source floor is not calibrated — set at least 4 point pairs first")
    return cal["H"], "floor", int(row.get("calibration_revision") or 0)


@router.post("/sources/{source_id}/project")
def project_points(source_id: int, body: ProjectIn):
    H, surface, revision = _projection(source_id, body.surface_id)
    pts = homography.project(H, body.points)
    return {"points": [{"x": p[0], "y": p[1]} for p in pts],
            "surface": surface, "surface_id": body.surface_id, "revision": revision}


@router.post("/sources/{source_id}/unproject")
def unproject_points(source_id: int, body: UnprojectIn):
    """Map metres -> camera pixels on the selected plane. A floor transform must
    not be used to compensate for the height of an elevated surface."""
    H, surface, revision = _projection(source_id, body.surface_id)
    try:
        inverse = homography.invert(H)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    pts = homography.project(inverse, body.points)
    return {"points": [{"x": p[0], "y": p[1]} for p in pts],
            "surface": surface, "surface_id": body.surface_id, "revision": revision}
