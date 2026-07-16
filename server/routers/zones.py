"""Named zones (checkout, entrance, fridge, aisle, ...) as polygons in map meters."""
import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import db

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
    polygon: list[dict]  # [{x,y}, ...] >= 3


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
    if len(body.polygon) < 3:
        raise HTTPException(422, "polygon needs at least 3 points")
    if body.ztype not in ZTYPES:
        raise HTTPException(422, f"ztype must be one of {sorted(ZTYPES)}")
    color = body.color or ZONE_COLORS[db.q1("SELECT COUNT(*) n FROM zones")["n"] % len(ZONE_COLORS)]
    zid = db.ex(
        "INSERT INTO zones (name, ztype, color, polygon_json, created_at) VALUES (?,?,?,?,?)",
        (body.name, body.ztype, color, json.dumps(body.polygon), db.now()),
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
