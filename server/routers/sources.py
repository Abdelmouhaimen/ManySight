"""Camera sources: CRUD, snapshots, map placement, homography calibration, projection."""
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from .. import db
from ..services import homography, snapshots

router = APIRouter(tags=["sources"])

KINDS = {"rtsp", "webrtc", "http", "webcam", "file"}


class SourceIn(BaseModel):
    name: str
    kind: str = "rtsp"
    url: str = ""
    username: str = ""
    password: str = ""
    extra: dict = {}


class SourcePatch(BaseModel):
    name: str | None = None
    kind: str | None = None
    url: str | None = None
    username: str | None = None
    password: str | None = None
    extra: dict | None = None


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


def serialize(row: dict, include_secrets: bool = False) -> dict:
    cal = db.jload(row.get("calibration_json"), None) if row.get("calibration_json") else None
    out = {
        "id": row["id"],
        "name": row["name"],
        "kind": row["kind"],
        "url": row["url"],
        "username": row["username"],
        "has_password": bool(row["password"]),
        "extra": db.jload(row["extra_json"], {}),
        "status": row["status"],
        "last_checked": row["last_checked"],
        "placement": (
            {"x": row["map_x"], "y": row["map_y"], "rotation_deg": row["rotation_deg"], "fov_deg": row["fov_deg"]}
            if row["map_x"] is not None else None
        ),
        "calibrated": bool(cal and cal.get("H")),
        "calibration": cal,
        "snapshot_url": f"/api/v1/sources/{row['id']}/snapshot.jpg",
        "created_at": row["created_at"],
    }
    if include_secrets:
        out["password"] = row["password"]
        out["connect_url"] = snapshots.connect_url(row)
    return out


def _get(source_id: int) -> dict:
    row = db.q1("SELECT * FROM sources WHERE id=?", (source_id,))
    if not row:
        raise HTTPException(404, "source not found")
    return row


@router.get("/sources")
def list_sources():
    return [serialize(r) for r in db.q("SELECT * FROM sources ORDER BY id")]


@router.post("/sources", status_code=201)
def create_source(body: SourceIn):
    if body.kind not in KINDS:
        raise HTTPException(422, f"kind must be one of {sorted(KINDS)}")
    import json
    sid = db.ex(
        "INSERT INTO sources (name, kind, url, username, password, extra_json, created_at) VALUES (?,?,?,?,?,?,?)",
        (body.name, body.kind, body.url, body.username, body.password, json.dumps(body.extra), db.now()),
    )
    return serialize(_get(sid))


@router.get("/sources/{source_id}")
def get_source(source_id: int, secrets: bool = False):
    return serialize(_get(source_id), include_secrets=secrets)


@router.put("/sources/{source_id}")
def update_source(source_id: int, body: SourcePatch):
    row = _get(source_id)
    import json
    fields = {
        "name": body.name, "kind": body.kind, "url": body.url,
        "username": body.username, "password": body.password,
        "extra_json": json.dumps(body.extra) if body.extra is not None else None,
    }
    sets = {k: v for k, v in fields.items() if v is not None}
    if body.kind is not None and body.kind not in KINDS:
        raise HTTPException(422, f"kind must be one of {sorted(KINDS)}")
    if sets:
        db.ex(f"UPDATE sources SET {', '.join(f'{k}=?' for k in sets)} WHERE id=?", (*sets.values(), source_id))
    return serialize(_get(source_id))


@router.delete("/sources/{source_id}")
def delete_source(source_id: int):
    _get(source_id)
    db.ex("DELETE FROM sources WHERE id=?", (source_id,))
    return {"deleted": source_id}


@router.post("/sources/{source_id}/snapshot")
def refresh_snapshot(source_id: int):
    row = _get(source_id)
    status, _ = snapshots.capture(row)
    db.ex("UPDATE sources SET status=?, last_checked=? WHERE id=?", (status, db.now(), source_id))
    return {"status": status, "snapshot_url": f"/api/v1/sources/{source_id}/snapshot.jpg"}


@router.get("/sources/{source_id}/snapshot.jpg")
def snapshot_image(source_id: int):
    row = _get(source_id)
    data, media = snapshots.get_snapshot_bytes(row)
    return Response(content=data, media_type=media, headers={"Cache-Control": "no-store"})


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
    _get(source_id)
    try:
        H, err = homography.compute_homography(body.points)
    except ValueError as e:
        raise HTTPException(422, str(e))
    import json
    cal = {"points": body.points, "H": H, "error_m": err, "frame_w": body.frame_w, "frame_h": body.frame_h}
    db.ex("UPDATE sources SET calibration_json=? WHERE id=?", (json.dumps(cal), source_id))
    return {"H": H, "error_m": err, "points": len(body.points)}


@router.delete("/sources/{source_id}/calibration")
def clear_calibration(source_id: int):
    _get(source_id)
    db.ex("UPDATE sources SET calibration_json=NULL WHERE id=?", (source_id,))
    return {"cleared": True}


@router.post("/sources/{source_id}/project")
def project_points(source_id: int, body: ProjectIn):
    row = _get(source_id)
    cal = db.jload(row.get("calibration_json"), None)
    if not cal or not cal.get("H"):
        raise HTTPException(409, "source is not calibrated — set at least 4 point pairs first")
    pts = homography.project(cal["H"], body.points)
    return {"points": [{"x": p[0], "y": p[1]} for p in pts]}
