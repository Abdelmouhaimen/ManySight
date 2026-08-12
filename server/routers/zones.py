"""Named zones (checkout, entrance, fridge, restricted, ...) as polygons in map meters.

A zone is pure geometry plus a semantic label — it carries no behavior of its own.
Alerts about a zone (e.g. "someone entered the restricted area") are separate
alert_rules; workers posting zone_enter/zone_exit don't know or care what the zone
means. Zones can be created from a camera-pixel polygon (`polygon_px` + `source_id`):
the platform projects it to map meters through the source's calibrated homography —
the path an agent uses after proposing a polygon from a locally captured frame."""
import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import db
from ..services import homography, zone_geometry

router = APIRouter(tags=["zones"])

# Dark-mode categorical palette, assigned in fixed slot order at creation time so a
# zone's color follows the zone forever (dataviz rule: color follows entity, not rank).
ZONE_COLORS = ["#3987e5", "#199e70", "#c98500", "#008300", "#9085e9", "#e66767", "#d55181", "#d95926"]
ZTYPES = {"checkout", "entrance", "fridge", "aisle", "area", "queue", "stockroom",
          "restricted", "equipment", "hall", "classroom", "playground", "meeting_room", "custom"}


class ZoneIn(BaseModel):
    name: str
    ztype: str = "area"
    color: str = ""
    polygon: list[dict] | None = None     # [{x,y}, ...] in map meters, >= 3
    geometry: dict | None = None          # GeoJSON Polygon | MultiPolygon in map meters
    polygon_px: list[dict] | None = None  # [{x,y}, ...] in camera pixels — projected server-side
    source_id: int | None = None          # required with polygon_px (must be calibrated)


class ZonePatch(BaseModel):
    name: str | None = None
    ztype: str | None = None
    color: str | None = None
    polygon: list[dict] | None = None
    geometry: dict | None = None


def serialize(row: dict) -> dict:
    geometry = db.jload(row.get("geometry_json"), None)
    if not geometry:
        polygon = db.jload(row["polygon_json"], [])
        geometry = zone_geometry.as_geojson(zone_geometry.polygon_from_points(polygon))
    return {"id": row["id"], "name": row["name"], "ztype": row["ztype"],
            "color": row["color"], "polygon": zone_geometry.legacy_exterior(geometry),
            "geometry": geometry, "component_count": zone_geometry.component_count(geometry),
            "revision": row.get("revision", 1), "updated_at": row.get("updated_at")}


@router.get("/zones")
def list_zones():
    return [serialize(r) for r in db.q("SELECT * FROM zones ORDER BY id")]


@router.get("/zones/{zone_id}")
def get_zone(zone_id: int):
    row = db.q1("SELECT * FROM zones WHERE id=?", (zone_id,))
    if not row:
        raise HTTPException(404, "zone not found")
    result = serialize(row)
    provenance = []
    for stored in db.q(
        "SELECT * FROM zone_geometry_provenance WHERE zone_id=? ORDER BY id", (zone_id,)
    ):
        item = dict(stored)
        item["original_pixel_polygon"] = db.jload(item.pop("original_pixel_polygon_json"), None)
        item["projected_map_polygon"] = db.jload(item.pop("projected_map_polygon_json"), [])
        source = (db.q1("SELECT calibration_revision FROM sources WHERE id=?", (item["source_id"],))
                  if item.get("source_id") is not None else None)
        view = (db.q1("SELECT revision FROM zone_views WHERE id=?", (item["zone_view_id"],))
                if item.get("zone_view_id") is not None else None)
        surface = (db.q1("SELECT revision FROM projection_surfaces WHERE id=?",
                         (item["projection_surface_id"],))
                   if item.get("projection_surface_id") is not None else None)
        item["stale"] = bool(
            (source and item.get("source_calibration_revision") != source["calibration_revision"])
            or (view and item.get("zone_view_revision") != view["revision"])
            or (surface and item.get("projection_surface_revision") != surface["revision"])
        )
        provenance.append(item)
    result["geometry_provenance"] = provenance
    return result


@router.post("/zones", status_code=201)
def create_zone(body: ZoneIn):
    polygon = body.polygon
    projected_from_pixels = False
    if polygon is None and body.polygon_px is not None:
        if body.source_id is None:
            raise HTTPException(422, "polygon_px requires source_id (the camera the pixels are from)")
        src = db.q1("SELECT calibration_json FROM sources WHERE id=?", (body.source_id,))
        if not src:
            raise HTTPException(404, f"source {body.source_id} not found")
        cal = db.jload(src["calibration_json"], None)
        if not cal or not cal.get("H"):
            raise HTTPException(409, f"source {body.source_id} is not calibrated — calibrate it "
                                     "in the Store Map tab, or pass a map-meter polygon instead")
        projected = homography.project(cal["H"], body.polygon_px)
        polygon = [{"x": round(x, 3), "y": round(y, 3)} for x, y in projected]
        projected_from_pixels = True
    try:
        if body.geometry is not None:
            canonical = zone_geometry.from_geojson(body.geometry)
        elif polygon is not None:
            canonical = zone_geometry.polygon_from_points(polygon)
        else:
            raise ValueError("provide geometry, polygon (map meters), or polygon_px + source_id")
        geometry = zone_geometry.as_geojson(canonical)
        polygon = zone_geometry.legacy_exterior(geometry)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    if body.ztype not in ZTYPES:
        raise HTTPException(422, f"ztype must be one of {sorted(ZTYPES)}")
    color = body.color or ZONE_COLORS[db.q1("SELECT COUNT(*) n FROM zones")["n"] % len(ZONE_COLORS)]
    now = db.now()
    zid = db.ex(
        "INSERT INTO zones (name, ztype, color, polygon_json, geometry_json, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (body.name, body.ztype, color, json.dumps(polygon), json.dumps(geometry), now, now),
    )
    if projected_from_pixels:
        source = db.q1("SELECT calibration_revision FROM sources WHERE id=?", (body.source_id,))
        db.ex(
            "INSERT INTO zone_geometry_provenance (zone_id,source_id,source_calibration_revision,"
            "original_pixel_polygon_json,projected_map_polygon_json,operation,resulting_zone_revision,created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (zid, body.source_id, source["calibration_revision"], json.dumps(body.polygon_px),
             json.dumps(polygon), "create_from_camera_polygon", 1, now),
        )
    return serialize(db.q1("SELECT * FROM zones WHERE id=?", (zid,)))


@router.put("/zones/{zone_id}")
def update_zone(zone_id: int, body: ZonePatch):
    row = db.q1("SELECT * FROM zones WHERE id=?", (zone_id,))
    if not row:
        raise HTTPException(404, "zone not found")
    sets, args = [], []
    if body.name is not None:
        sets.append("name=?"); args.append(body.name)
    if body.ztype is not None:
        if body.ztype not in ZTYPES:
            raise HTTPException(422, f"ztype must be one of {sorted(ZTYPES)}")
        sets.append("ztype=?"); args.append(body.ztype)
    if body.color is not None:
        sets.append("color=?"); args.append(body.color)
    if body.polygon is not None or body.geometry is not None:
        try:
            canonical = (zone_geometry.from_geojson(body.geometry) if body.geometry is not None
                         else zone_geometry.polygon_from_points(body.polygon))
            geometry = zone_geometry.as_geojson(canonical)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        sets.extend(["polygon_json=?", "geometry_json=?"])
        args.extend([json.dumps(zone_geometry.legacy_exterior(geometry)), json.dumps(geometry)])
    if sets:
        sets.extend(["revision=revision+1", "updated_at=?"]); args.append(db.now())
        db.ex(f"UPDATE zones SET {', '.join(sets)} WHERE id=?", (*args, zone_id))
    return serialize(db.q1("SELECT * FROM zones WHERE id=?", (zone_id,)))


@router.delete("/zones/{zone_id}")
def delete_zone(zone_id: int):
    if not db.q1("SELECT id FROM zones WHERE id=?", (zone_id,)):
        raise HTTPException(404, "zone not found")
    db.ex("DELETE FROM zone_views WHERE zone_id=?", (zone_id,))
    db.ex("DELETE FROM zones WHERE id=?", (zone_id,))
    return {"deleted": zone_id}
