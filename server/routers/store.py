"""Store floor plan: name, dimensions (meters), walls and text labels."""
import io
import json
import math
import zipfile

from fastapi import APIRouter, File, HTTPException, UploadFile
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


@router.post("/store/import-metric-blueprint")
async def import_metric_blueprint(bundle: UploadFile = File(...)):
    """Import map-only metric geometry from plan_blueprint_digitizer."""
    payload = await bundle.read()
    if len(payload) > 25 * 1024 * 1024:
        raise HTTPException(413, "blueprint ZIP exceeds the 25 MB limit")
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            names = archive.namelist()
            for name in names:
                parts = name.replace("\\", "/").split("/")
                if name.startswith(("/", "\\")) or ".." in parts:
                    raise HTTPException(422, "blueprint ZIP contains an unsafe path")
            if "floor_polygon.json" not in names:
                raise HTTPException(422, "blueprint ZIP is missing floor_polygon.json")
            info = archive.getinfo("floor_polygon.json")
            if info.file_size > 2 * 1024 * 1024:
                raise HTTPException(422, "floor_polygon.json is too large")
            document = json.loads(archive.read("floor_polygon.json"))
    except HTTPException:
        raise
    except (zipfile.BadZipFile, json.JSONDecodeError, KeyError) as exc:
        raise HTTPException(422, f"invalid metric-blueprint ZIP: {exc}") from exc

    units = document.get("coordinate_system", {}).get("units")
    if units not in {"meters", "metres", "m"}:
        raise HTTPException(422, "floor_polygon.json must use metres")
    polygons = document.get("polygons")
    if not isinstance(polygons, list) or not polygons:
        raise HTTPException(422, "floor_polygon.json must contain at least one polygon")

    clean = []
    for polygon in polygons:
        if not isinstance(polygon, list) or len(polygon) < 3:
            raise HTTPException(422, "every floor polygon needs at least three points")
        converted = []
        for point in polygon:
            try:
                x, y = float(point["x"]), float(point["y"])
            except (KeyError, TypeError, ValueError) as exc:
                raise HTTPException(422, "floor coordinates must be finite numbers") from exc
            if not math.isfinite(x) or not math.isfinite(y):
                raise HTTPException(422, "floor coordinates must be finite numbers")
            converted.append({"x": x, "y": y})
        clean.append(converted)

    all_points = [point for polygon in clean for point in polygon]
    min_x = min(point["x"] for point in all_points)
    max_x = max(point["x"] for point in all_points)
    min_y = min(point["y"] for point in all_points)
    max_y = max(point["y"] for point in all_points)
    width_m, height_m = max_x - min_x, max_y - min_y
    if width_m <= 1e-6 or height_m <= 1e-6:
        raise HTTPException(422, "floor polygon has zero width or height")

    y_up = document.get("coordinate_system", {}).get("y_axis") == "up"
    normalized = [[{
        "x": round(point["x"] - min_x, 6),
        "y": round(max_y - point["y"] if y_up else point["y"] - min_y, 6),
    } for point in polygon] for polygon in clean]

    current = serialize(db.q1("SELECT * FROM stores WHERE id=1"))
    current_map = current.get("map") or {}
    next_map = {
        **current_map,
        "floor_polygons": normalized,
        "walls": [polygon + [polygon[0]] for polygon in normalized],
        "blueprint_import": {
            "schema_version": document.get("schema_version", 1),
            "source_filename": bundle.filename,
            "known_distance_m": document.get("scale", {}).get("known_distance_m"),
        },
    }
    db.ex(
        "UPDATE stores SET width_m=?, height_m=?, map_json=? WHERE id=1",
        (width_m, height_m, json.dumps(next_map)),
    )
    invalidated_calibrations = db.q1(
        "SELECT COUNT(*) AS total FROM sources WHERE calibration_json IS NOT NULL"
    )["total"]
    cleared_placements = db.q1(
        "SELECT COUNT(*) AS total FROM sources WHERE map_x IS NOT NULL"
    )["total"]
    db.ex(
        "UPDATE sources SET calibration_json=NULL, calibration_revision=calibration_revision+1 "
        "WHERE calibration_json IS NOT NULL"
    )
    db.ex("UPDATE sources SET map_x=NULL, map_y=NULL WHERE map_x IS NOT NULL")
    return {
        "store": serialize(db.q1("SELECT * FROM stores WHERE id=1")),
        "polygon_count": len(normalized),
        "width_m": width_m,
        "height_m": height_m,
        "invalidated_calibrations": invalidated_calibrations,
        "cleared_placements": cleared_placements,
    }
