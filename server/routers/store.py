"""Store floor plan: name, dimensions (meters), walls and text labels."""
import json

from fastapi import APIRouter
from pydantic import BaseModel

from .. import db

router = APIRouter(tags=["store"])


class StorePatch(BaseModel):
    name: str | None = None
    space_type: str | None = None
    environment: str | None = None
    width_m: float | None = None
    height_m: float | None = None
    map: dict | None = None  # {"walls": [[{x,y},...]], "labels": [{x,y,text}]}


def serialize(row: dict) -> dict:
    return {
        "id": row["id"], "name": row["name"],
        "space_type": row.get("space_type") or "store",
        "environment": row.get("environment") or "setup",
        "width_m": row["width_m"], "height_m": row["height_m"],
        "map": db.jload(row["map_json"], {}),
    }


@router.get("/store")
def get_store():
    return serialize(db.q1("SELECT * FROM stores WHERE id=1"))


@router.put("/store")
def update_store(body: StorePatch):
    sets, args = [], []
    if body.name is not None:
        sets.append("name=?"); args.append(body.name)
    if body.space_type is not None:
        sets.append("space_type=?"); args.append(body.space_type)
    if body.environment is not None:
        if body.environment not in {"setup", "demo", "live"}:
            from fastapi import HTTPException
            raise HTTPException(422, "environment must be setup, demo, or live")
        sets.append("environment=?"); args.append(body.environment)
    if body.width_m is not None:
        sets.append("width_m=?"); args.append(body.width_m)
    if body.height_m is not None:
        sets.append("height_m=?"); args.append(body.height_m)
    if body.map is not None:
        sets.append("map_json=?"); args.append(json.dumps(body.map))
    if sets:
        db.ex(f"UPDATE stores SET {', '.join(sets)} WHERE id=1", args)
    return serialize(db.q1("SELECT * FROM stores WHERE id=1"))
