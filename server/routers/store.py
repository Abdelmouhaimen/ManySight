"""Store floor plan: name, dimensions (meters), walls and text labels."""
import json
import math

from fastapi import APIRouter, HTTPException
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


class BlueprintPoint(BaseModel):
    x: float
    y: float


class BlueprintTrace(BaseModel):
    image_width: int
    image_height: int
    polygons_px: list[list[BlueprintPoint]]
    scale_points_px: list[BlueprintPoint]
    known_distance_m: float
    origin_px: BlueprintPoint
    y_axis_up: bool = True


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


@router.post("/store/blueprint")
def save_blueprint(body: BlueprintTrace):
    """Convert a browser-side plan trace into StoreLens metric map geometry.

    The source image never reaches this endpoint; only pixel coordinates and a
    user-confirmed distance are persisted.
    """
    if body.image_width < 2 or body.image_height < 2:
        raise HTTPException(422, "image dimensions must be at least 2 by 2 pixels")
    if not math.isfinite(body.known_distance_m) or body.known_distance_m <= 0:
        raise HTTPException(422, "known distance must be a positive finite number")
    if len(body.scale_points_px) != 2:
        raise HTTPException(422, "exactly two scale points are required")
    if not body.polygons_px:
        raise HTTPException(422, "at least one floor polygon is required")
    if any(len(polygon) < 3 for polygon in body.polygons_px):
        raise HTTPException(422, "every floor polygon needs at least three points")

    supplied_points = [
        *body.scale_points_px,
        body.origin_px,
        *(point for polygon in body.polygons_px for point in polygon),
    ]
    if any(not math.isfinite(point.x) or not math.isfinite(point.y) for point in supplied_points):
        raise HTTPException(422, "blueprint coordinates must be finite numbers")
    pixel_distance = math.hypot(
        body.scale_points_px[1].x - body.scale_points_px[0].x,
        body.scale_points_px[1].y - body.scale_points_px[0].y,
    )
    if pixel_distance <= 1e-6:
        raise HTTPException(422, "scale points must be distinct")
    pixels_per_metre = pixel_distance / body.known_distance_m

    metric = [[{
        "x": (point.x - body.origin_px.x) / pixels_per_metre,
        "y": ((body.origin_px.y - point.y) if body.y_axis_up else (point.y - body.origin_px.y)) / pixels_per_metre,
    } for point in polygon] for polygon in body.polygons_px]
    all_points = [point for polygon in metric for point in polygon]
    min_x = min(point["x"] for point in all_points)
    max_x = max(point["x"] for point in all_points)
    min_y = min(point["y"] for point in all_points)
    max_y = max(point["y"] for point in all_points)
    width_m, height_m = max_x - min_x, max_y - min_y
    if width_m <= 1e-6 or height_m <= 1e-6:
        raise HTTPException(422, "floor polygon has zero width or height")
    normalized = [[{
        "x": round(point["x"] - min_x, 6),
        "y": round(max_y - point["y"] if body.y_axis_up else point["y"] - min_y, 6),
    } for point in polygon] for polygon in metric]

    con = db.connect()
    try:
        current = serialize(dict(con.execute("SELECT * FROM stores WHERE id=1").fetchone()))
        current_map = current.get("map") or {}
        next_map = {
            **current_map,
            "floor_polygons": normalized,
            "walls": [polygon + [polygon[0]] for polygon in normalized],
            "blueprint_trace": {
                "schema_version": 1,
                "coordinate_system": {
                    "units": "meters",
                    "x_axis": "image-right",
                    "y_axis": "up" if body.y_axis_up else "image-down",
                },
                "source_image": {"width": body.image_width, "height": body.image_height},
                "scale": {
                    "pixels_per_meter": pixels_per_metre,
                    "known_distance_m": body.known_distance_m,
                    "origin_pixel": body.origin_px.model_dump(),
                },
            },
        }
        invalidated_calibrations = con.execute(
            "SELECT COUNT(*) FROM sources WHERE calibration_json IS NOT NULL"
        ).fetchone()[0]
        cleared_placements = con.execute(
            "SELECT COUNT(*) FROM sources WHERE map_x IS NOT NULL"
        ).fetchone()[0]
        con.execute(
            "UPDATE stores SET width_m=?, height_m=?, map_json=? WHERE id=1",
            (width_m, height_m, json.dumps(next_map)),
        )
        con.execute(
            "UPDATE sources SET calibration_json=NULL, calibration_revision=calibration_revision+1 "
            "WHERE calibration_json IS NOT NULL"
        )
        con.execute("UPDATE sources SET map_x=NULL, map_y=NULL WHERE map_x IS NOT NULL")
        con.commit()
    finally:
        con.close()
    return {
        "store": serialize(db.q1("SELECT * FROM stores WHERE id=1")),
        "polygon_count": len(normalized),
        "width_m": width_m,
        "height_m": height_m,
        "invalidated_calibrations": invalidated_calibrations,
        "cleared_placements": cleared_placements,
    }
