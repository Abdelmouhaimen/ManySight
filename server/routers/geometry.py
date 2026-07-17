"""Plane-aware camera geometry: projection surfaces and per-camera zone views.

The global zone polygon remains the physical map footprint. A zone view stores how
that zone appears in one camera and which inset ROI/rule should decide membership.
Projection surfaces provide additional pixel->map homographies for elevated planes
such as a mattress or table; height is metadata, never subtracted from map Y.
"""
import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import db
from ..services import homography

router = APIRouter(tags=["geometry"])

SURFACE_KINDS = {"mattress", "table", "shelf", "conveyor", "platform", "custom"}
MEMBERSHIP_RULES = {"point", "bbox_overlap", "keypoints_inside"}


class ProjectionSurfaceIn(BaseModel):
    source_id: int
    name: str
    kind: str = "custom"
    height_m: float | None = None
    points: list[dict]
    frame_w: int | None = None
    frame_h: int | None = None


class ProjectionSurfacePatch(BaseModel):
    name: str | None = None
    kind: str | None = None
    height_m: float | None = None
    points: list[dict] | None = None
    frame_w: int | None = None
    frame_h: int | None = None


class ZoneViewIn(BaseModel):
    zone_id: int
    source_id: int
    outer_polygon_px: list[dict]
    detection_polygon_px: list[dict] | None = None
    projection_surface_id: int | None = None
    membership_rule: str = "point"
    threshold: float = 0.5
    min_keypoints: int = 1


class ZoneViewPatch(BaseModel):
    outer_polygon_px: list[dict] | None = None
    detection_polygon_px: list[dict] | None = None
    projection_surface_id: int | None = None
    membership_rule: str | None = None
    threshold: float | None = None
    min_keypoints: int | None = None


def _source(source_id: int) -> dict:
    row = db.q1("SELECT id, name FROM sources WHERE id=?", (source_id,))
    if not row:
        raise HTTPException(404, f"source {source_id} not found")
    return row


def _zone(zone_id: int) -> dict:
    row = db.q1("SELECT id, name, revision FROM zones WHERE id=?", (zone_id,))
    if not row:
        raise HTTPException(404, f"zone {zone_id} not found")
    return row


def _surface(surface_id: int) -> dict:
    row = db.q1("SELECT * FROM projection_surfaces WHERE id=?", (surface_id,))
    if not row:
        raise HTTPException(404, f"projection surface {surface_id} not found")
    return row


def serialize_surface(row: dict) -> dict:
    return {
        "id": row["id"], "source_id": row["source_id"], "name": row["name"],
        "kind": row["kind"], "height_m": row["height_m"],
        "points": db.jload(row["points_json"], []),
        "H": db.jload(row["homography_json"], None), "error_m": row["error_m"],
        "frame_w": row["frame_w"], "frame_h": row["frame_h"],
        "revision": row["revision"], "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def serialize_zone_view(row: dict) -> dict:
    return {
        "id": row["id"], "zone_id": row["zone_id"], "source_id": row["source_id"],
        "zone_name": row.get("zone_name"), "source_name": row.get("source_name"),
        "outer_polygon_px": db.jload(row["outer_polygon_json"], []),
        "detection_polygon_px": db.jload(row["detection_polygon_json"], []),
        "projection_surface_id": row["projection_surface_id"],
        "projection_surface_name": row.get("surface_name"),
        "membership_rule": row["membership_rule"], "threshold": row["threshold"],
        "min_keypoints": row["min_keypoints"], "revision": row["revision"],
        "created_at": row["created_at"], "updated_at": row["updated_at"],
    }


def _validate_surface(kind: str, points: list[dict]):
    if kind not in SURFACE_KINDS:
        raise HTTPException(422, f"kind must be one of {sorted(SURFACE_KINDS)}")
    if len(points) < 4:
        raise HTTPException(422, "projection surface needs at least four pixel/map point pairs")


def _validate_zone_view(source_id: int, zone_id: int, outer: list[dict], detection: list[dict],
                        surface_id: int | None, rule: str, threshold: float,
                        min_keypoints: int):
    _source(source_id); _zone(zone_id)
    if len(outer) < 3 or len(detection) < 3:
        raise HTTPException(422, "outer and detection polygons need at least three points")
    if rule not in MEMBERSHIP_RULES:
        raise HTTPException(422, f"membership_rule must be one of {sorted(MEMBERSHIP_RULES)}")
    if not 0 <= threshold <= 1:
        raise HTTPException(422, "threshold must be between 0 and 1")
    if min_keypoints < 1:
        raise HTTPException(422, "min_keypoints must be at least 1")
    if surface_id is not None:
        surface = _surface(surface_id)
        if surface["source_id"] != source_id:
            raise HTTPException(422, "projection surface belongs to a different source")


@router.get("/projection-surfaces")
def list_projection_surfaces(source_id: int | None = None):
    where, args = ("WHERE source_id=?", (source_id,)) if source_id is not None else ("", ())
    return [serialize_surface(r) for r in db.q(
        f"SELECT * FROM projection_surfaces {where} ORDER BY source_id, name", args)]


@router.post("/projection-surfaces", status_code=201)
def create_projection_surface(body: ProjectionSurfaceIn):
    _source(body.source_id)
    _validate_surface(body.kind, body.points)
    if db.q1("SELECT id FROM projection_surfaces WHERE source_id=? AND lower(name)=lower(?)",
             (body.source_id, body.name)):
        raise HTTPException(409, "a projection surface with this name already exists for the source")
    try:
        H, error = homography.compute_homography(body.points)
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(422, f"invalid point pairs: {exc}")
    now = db.now()
    sid = db.ex(
        "INSERT INTO projection_surfaces (source_id,name,kind,height_m,points_json,homography_json,"
        " error_m,frame_w,frame_h,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (body.source_id, body.name, body.kind, body.height_m, json.dumps(body.points),
         json.dumps(H), error, body.frame_w, body.frame_h, now, now))
    return serialize_surface(_surface(sid))


@router.get("/projection-surfaces/{surface_id}")
def get_projection_surface(surface_id: int):
    return serialize_surface(_surface(surface_id))


@router.put("/projection-surfaces/{surface_id}")
def update_projection_surface(surface_id: int, body: ProjectionSurfacePatch):
    row = _surface(surface_id)
    name, kind = body.name or row["name"], body.kind or row["kind"]
    points = body.points if body.points is not None else db.jload(row["points_json"], [])
    _validate_surface(kind, points)
    duplicate = db.q1(
        "SELECT id FROM projection_surfaces WHERE source_id=? AND lower(name)=lower(?) AND id!=?",
        (row["source_id"], name, surface_id))
    if duplicate:
        raise HTTPException(409, "a projection surface with this name already exists for the source")
    try:
        H, error = homography.compute_homography(points)
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(422, f"invalid point pairs: {exc}")
    height = body.height_m if "height_m" in body.model_fields_set else row["height_m"]
    frame_w = body.frame_w if "frame_w" in body.model_fields_set else row["frame_w"]
    frame_h = body.frame_h if "frame_h" in body.model_fields_set else row["frame_h"]
    db.ex(
        "UPDATE projection_surfaces SET name=?,kind=?,height_m=?,points_json=?,homography_json=?,"
        " error_m=?,frame_w=?,frame_h=?,revision=revision+1,updated_at=? WHERE id=?",
        (name, kind, height, json.dumps(points), json.dumps(H), error, frame_w, frame_h,
         db.now(), surface_id))
    return serialize_surface(_surface(surface_id))


@router.delete("/projection-surfaces/{surface_id}")
def delete_projection_surface(surface_id: int):
    _surface(surface_id)
    if db.q1("SELECT id FROM zone_views WHERE projection_surface_id=?", (surface_id,)):
        raise HTTPException(409, "projection surface is used by a zone view")
    db.ex("DELETE FROM projection_surfaces WHERE id=?", (surface_id,))
    return {"deleted": surface_id}


ZONE_VIEW_SELECT = """
SELECT zv.*, z.name zone_name, s.name source_name, ps.name surface_name
FROM zone_views zv
JOIN zones z ON z.id=zv.zone_id
JOIN sources s ON s.id=zv.source_id
LEFT JOIN projection_surfaces ps ON ps.id=zv.projection_surface_id
"""


@router.get("/zone-views")
def list_zone_views(source_id: int | None = None, zone_id: int | None = None):
    where, args = [], []
    if source_id is not None:
        where.append("zv.source_id=?"); args.append(source_id)
    if zone_id is not None:
        where.append("zv.zone_id=?"); args.append(zone_id)
    clause = "WHERE " + " AND ".join(where) if where else ""
    return [serialize_zone_view(r) for r in db.q(
        f"{ZONE_VIEW_SELECT} {clause} ORDER BY zv.source_id, zv.zone_id", args)]


@router.post("/zone-views", status_code=201)
def create_zone_view(body: ZoneViewIn):
    detection = body.detection_polygon_px or body.outer_polygon_px
    _validate_zone_view(body.source_id, body.zone_id, body.outer_polygon_px, detection,
                        body.projection_surface_id, body.membership_rule, body.threshold,
                        body.min_keypoints)
    if db.q1("SELECT id FROM zone_views WHERE zone_id=? AND source_id=?",
             (body.zone_id, body.source_id)):
        raise HTTPException(409, "this zone already has a view for the selected source")
    now = db.now()
    vid = db.ex(
        "INSERT INTO zone_views (zone_id,source_id,outer_polygon_json,detection_polygon_json,"
        " projection_surface_id,membership_rule,threshold,min_keypoints,created_at,updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (body.zone_id, body.source_id, json.dumps(body.outer_polygon_px), json.dumps(detection),
         body.projection_surface_id, body.membership_rule, body.threshold, body.min_keypoints,
         now, now))
    row = db.q1(f"{ZONE_VIEW_SELECT} WHERE zv.id=?", (vid,))
    return serialize_zone_view(row)


@router.get("/zone-views/{view_id}")
def get_zone_view(view_id: int):
    row = db.q1(f"{ZONE_VIEW_SELECT} WHERE zv.id=?", (view_id,))
    if not row:
        raise HTTPException(404, "zone view not found")
    return serialize_zone_view(row)


@router.put("/zone-views/{view_id}")
def update_zone_view(view_id: int, body: ZoneViewPatch):
    row = db.q1("SELECT * FROM zone_views WHERE id=?", (view_id,))
    if not row:
        raise HTTPException(404, "zone view not found")
    outer = body.outer_polygon_px or db.jload(row["outer_polygon_json"], [])
    detection = body.detection_polygon_px or db.jload(row["detection_polygon_json"], [])
    surface_id = (body.projection_surface_id if "projection_surface_id" in body.model_fields_set
                  else row["projection_surface_id"])
    rule = body.membership_rule or row["membership_rule"]
    threshold = body.threshold if body.threshold is not None else row["threshold"]
    min_keypoints = body.min_keypoints if body.min_keypoints is not None else row["min_keypoints"]
    _validate_zone_view(row["source_id"], row["zone_id"], outer, detection, surface_id,
                        rule, threshold, min_keypoints)
    db.ex(
        "UPDATE zone_views SET outer_polygon_json=?,detection_polygon_json=?,"
        " projection_surface_id=?,membership_rule=?,threshold=?,min_keypoints=?,"
        " revision=revision+1,updated_at=? WHERE id=?",
        (json.dumps(outer), json.dumps(detection), surface_id, rule, threshold,
         min_keypoints, db.now(), view_id))
    return get_zone_view(view_id)


@router.delete("/zone-views/{view_id}")
def delete_zone_view(view_id: int):
    if not db.q1("SELECT id FROM zone_views WHERE id=?", (view_id,)):
        raise HTTPException(404, "zone view not found")
    db.ex("DELETE FROM zone_views WHERE id=?", (view_id,))
    return {"deleted": view_id}
