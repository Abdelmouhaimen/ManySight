"""Named zones (checkout, entrance, fridge, restricted, ...) as polygons in map meters.

A zone is pure geometry plus a semantic label — it carries no behavior of its own.
Alerts about a zone (e.g. "someone entered the restricted area") are separate
alert_rules; workers posting zone_enter/zone_exit don't know or care what the zone
means. Zones can be created from a camera-pixel polygon (`polygon_px` + `source_id`):
the platform projects it to map meters through the source's calibrated homography —
the path an agent uses after proposing a polygon from a snapshot."""
import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import db
from ..services import homography

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
    polygon_px: list[dict] | None = None  # [{x,y}, ...] in camera pixels — projected server-side
    source_id: int | None = None          # required with polygon_px (must be calibrated)


class ZonePatch(BaseModel):
    name: str | None = None
    ztype: str | None = None
    color: str | None = None
    polygon: list[dict] | None = None


def serialize(row: dict) -> dict:
    return {"id": row["id"], "name": row["name"], "ztype": row["ztype"],
            "color": row["color"], "polygon": db.jload(row["polygon_json"], [])}


@router.get("/zones")
def list_zones():
    return [serialize(r) for r in db.q("SELECT * FROM zones ORDER BY id")]


@router.post("/zones", status_code=201)
def create_zone(body: ZoneIn):
    polygon = body.polygon
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
    if polygon is None:
        raise HTTPException(422, "provide polygon (map meters) or polygon_px + source_id")
    if len(polygon) < 3:
        raise HTTPException(422, "polygon needs at least 3 points")
    if body.ztype not in ZTYPES:
        raise HTTPException(422, f"ztype must be one of {sorted(ZTYPES)}")
    color = body.color or ZONE_COLORS[db.q1("SELECT COUNT(*) n FROM zones")["n"] % len(ZONE_COLORS)]
    zid = db.ex(
        "INSERT INTO zones (name, ztype, color, polygon_json, created_at) VALUES (?,?,?,?,?)",
        (body.name, body.ztype, color, json.dumps(polygon), db.now()),
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
    if body.polygon is not None:
        if len(body.polygon) < 3:
            raise HTTPException(422, "polygon needs at least 3 points")
        sets.append("polygon_json=?"); args.append(json.dumps(body.polygon))
    if sets:
        db.ex(f"UPDATE zones SET {', '.join(sets)} WHERE id=?", (*args, zone_id))
    return serialize(db.q1("SELECT * FROM zones WHERE id=?", (zone_id,)))


@router.delete("/zones/{zone_id}")
def delete_zone(zone_id: int):
    if not db.q1("SELECT id FROM zones WHERE id=?", (zone_id,)):
        raise HTTPException(404, "zone not found")
    db.ex("DELETE FROM zones WHERE id=?", (zone_id,))
    return {"deleted": zone_id}
