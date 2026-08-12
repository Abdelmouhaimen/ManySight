"""Provider-neutral rich camera calibration import with NVIDIA MV3DT adapter."""
from __future__ import annotations

import json
import math

import numpy as np
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import db
router = APIRouter(tags=["calibration"])
SUPPORTED_PROVIDERS = {"generic", "nvidia_mv3dt", "nvidia_amc"}


class CalibrationImportIn(BaseModel):
    source_id: int
    provider: str = "generic"
    projection_matrix: list
    world_to_map_transform: list = Field(
        default_factory=lambda: [[1, 0, 0], [0, 1, 0], [0, 0, 1]])
    units: str
    world_frame: dict
    ground_plane_z: float = 0.0
    frame_w: int | None = None
    frame_h: int | None = None
    distortion: list | dict = Field(default_factory=list)
    intrinsics: dict = Field(default_factory=dict)
    extrinsics: dict = Field(default_factory=dict)
    verification_points: list[dict] = Field(default_factory=list)


def _matrix(value: list) -> np.ndarray:
    array = np.asarray(value, dtype=float)
    if array.size != 12:
        raise ValueError("projection_matrix must contain exactly 12 values (3x4)")
    array = array.reshape(3, 4)
    if not np.isfinite(array).all() or np.linalg.matrix_rank(array) < 3:
        raise ValueError("projection_matrix must be finite and rank 3")
    return array


def _effective_projection(projection: np.ndarray, transform_value: list) -> tuple[np.ndarray, np.ndarray]:
    transform = np.asarray(transform_value, dtype=float)
    if transform.shape != (3, 3) or not np.isfinite(transform).all():
        raise ValueError("world_to_map_transform must be a finite 3x3 matrix")
    if abs(np.linalg.det(transform)) < 1e-12:
        raise ValueError("world_to_map_transform must be invertible")
    if not np.allclose(transform[2], [0, 0, 1], atol=1e-9):
        raise ValueError("world_to_map_transform must be an affine ground-plane transform")
    map_to_world = np.linalg.inv(transform)
    conversion = np.array([
        [map_to_world[0, 0], map_to_world[0, 1], 0, map_to_world[0, 2]],
        [map_to_world[1, 0], map_to_world[1, 1], 0, map_to_world[1, 2]],
        [0, 0, 1, 0],
        [0, 0, 0, 1],
    ], dtype=float)
    return projection @ conversion, transform


def derive_ground_homography(matrix: list, ground_plane_z: float = 0.0) -> tuple[list, list]:
    projection = _matrix(matrix)
    if not math.isfinite(ground_plane_z):
        raise ValueError("ground_plane_z must be finite")
    world_to_pixel = np.column_stack((
        projection[:, 0], projection[:, 1],
        projection[:, 2] * ground_plane_z + projection[:, 3],
    ))
    if abs(np.linalg.det(world_to_pixel)) < 1e-12:
        raise ValueError("ground-plane projection is singular")
    pixel_to_world = np.linalg.inv(world_to_pixel)
    world_to_pixel /= world_to_pixel[2, 2]
    pixel_to_world /= pixel_to_world[2, 2]
    return world_to_pixel.tolist(), pixel_to_world.tolist()


def _verify(body: CalibrationImportIn, matrix: np.ndarray) -> dict:
    errors = []
    for point in body.verification_points:
        world, pixel = point.get("world"), point.get("pixel")
        if not world or len(world) not in {2, 3} or not pixel or len(pixel) != 2:
            raise ValueError("verification points require world [x,y,z?] and pixel [u,v]")
        xyz = [float(world[0]), float(world[1]),
               float(world[2]) if len(world) == 3 else body.ground_plane_z, 1.0]
        projected = matrix @ np.asarray(xyz)
        if abs(projected[2]) < 1e-12:
            raise ValueError("verification point projects to infinity")
        uv = projected[:2] / projected[2]
        errors.append(float(np.linalg.norm(uv - np.asarray(pixel, dtype=float))))
    return {
        "status": "verified" if errors else "unverified",
        "point_count": len(errors),
        "mean_reprojection_error_px": sum(errors) / len(errors) if errors else None,
        "max_reprojection_error_px": max(errors) if errors else None,
    }


def serialize(row: dict) -> dict:
    return {
        "id": row["id"], "source_id": row["source_id"], "provider": row["provider"],
        "original_projection_matrix": db.jload(
            row.get("original_projection_matrix_json"),
            db.jload(row["projection_matrix_json"], []),
        ),
        "projection_matrix": db.jload(row["projection_matrix_json"], []),
        "world_to_map_transform": db.jload(
            row.get("world_to_map_transform_json"), [[1, 0, 0], [0, 1, 0], [0, 0, 1]]),
        "distortion": db.jload(row["distortion_json"], []),
        "intrinsics": db.jload(row["intrinsics_json"], {}),
        "extrinsics": db.jload(row["extrinsics_json"], {}),
        "world_frame": db.jload(row["world_frame_json"], {}),
        "ground_plane_z": row["ground_plane_z"], "units": row["units"],
        "frame_w": row["frame_w"], "frame_h": row["frame_h"],
        "floor_homography": db.jload(row["derived_homography_json"], {}),
        "verification": db.jload(row["verification_json"], {}),
        "revision": row["revision"], "created_at": row["created_at"], "updated_at": row["updated_at"],
    }


@router.get("/calibrations")
def list_calibrations(source_id: int | None = None):
    clause, args = ("WHERE source_id=?", (source_id,)) if source_id is not None else ("", ())
    return [serialize(row) for row in db.q(
        f"SELECT * FROM camera_calibrations {clause} ORDER BY source_id", args)]


@router.get("/calibrations/{calibration_id}")
def get_calibration(calibration_id: int):
    row = db.q1("SELECT * FROM camera_calibrations WHERE id=?", (calibration_id,))
    if not row:
        raise HTTPException(404, "calibration not found")
    return serialize(row)


@router.post("/calibrations/import", status_code=201)
def import_calibration(body: CalibrationImportIn):
    source = db.q1("SELECT * FROM sources WHERE id=?", (body.source_id,))
    if not source:
        raise HTTPException(404, "source not found")
    if body.provider not in SUPPORTED_PROVIDERS:
        raise HTTPException(422, f"provider must be one of {sorted(SUPPORTED_PROVIDERS)}")
    if body.units not in {"m", "meter", "metre", "meters", "metres"}:
        raise HTTPException(422, "world units must be metres for StoreLens shared geometry")
    if not body.world_frame.get("name") or not body.world_frame.get("axes"):
        raise HTTPException(422, "world_frame requires a name and explicit axes metadata")
    if (body.frame_w is None) != (body.frame_h is None) or (body.frame_w is not None and
                                                            (body.frame_w <= 0 or body.frame_h <= 0)):
        raise HTTPException(422, "frame_w and frame_h must be positive and supplied together")
    try:
        original_matrix = _matrix(body.projection_matrix)
        matrix, world_to_map = _effective_projection(original_matrix, body.world_to_map_transform)
        world_to_pixel, pixel_to_world = derive_ground_homography(
            matrix.tolist(), body.ground_plane_z)
        verification = _verify(body, matrix)
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, str(exc))
    now = db.now()
    existing = db.q1("SELECT id,revision,created_at FROM camera_calibrations WHERE source_id=?",
                     (body.source_id,))
    revision = (existing["revision"] + 1) if existing else 1
    floor = {"world_to_pixel": world_to_pixel, "pixel_to_world": pixel_to_world}
    if existing:
        db.ex(
            "UPDATE camera_calibrations SET provider=?,original_projection_matrix_json=?,"
            "projection_matrix_json=?,world_to_map_transform_json=?,distortion_json=?,"
            "intrinsics_json=?,extrinsics_json=?,world_frame_json=?,ground_plane_z=?,units=?,frame_w=?,"
            "frame_h=?,derived_homography_json=?,verification_json=?,revision=?,updated_at=? WHERE id=?",
            (body.provider, json.dumps(original_matrix.tolist()), json.dumps(matrix.tolist()),
             json.dumps(world_to_map.tolist()), json.dumps(body.distortion),
             json.dumps(body.intrinsics), json.dumps(body.extrinsics), json.dumps(body.world_frame),
             body.ground_plane_z, "m", body.frame_w, body.frame_h, json.dumps(floor),
             json.dumps(verification), revision, now, existing["id"]),
        )
        calibration_id = existing["id"]
    else:
        calibration_id = db.ex(
            "INSERT INTO camera_calibrations (source_id,provider,original_projection_matrix_json,"
            "projection_matrix_json,world_to_map_transform_json,distortion_json,"
            "intrinsics_json,extrinsics_json,world_frame_json,ground_plane_z,units,frame_w,frame_h,"
            "derived_homography_json,verification_json,revision,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (body.source_id, body.provider, json.dumps(original_matrix.tolist()),
             json.dumps(matrix.tolist()), json.dumps(world_to_map.tolist()), json.dumps(body.distortion),
             json.dumps(body.intrinsics), json.dumps(body.extrinsics), json.dumps(body.world_frame),
             body.ground_plane_z, "m", body.frame_w, body.frame_h, json.dumps(floor),
             json.dumps(verification), revision, now, now),
        )
    # Preserve the existing planar interface for enrichment and Setup/Live.
    calibration = {
        "H": pixel_to_world, "H_map_to_pixel": world_to_pixel,
        "frame_w": body.frame_w, "frame_h": body.frame_h,
        "provider": body.provider, "rich_calibration_id": calibration_id,
        "world_frame": body.world_frame, "units": "m", "ground_plane_z": body.ground_plane_z,
    }
    db.ex("UPDATE sources SET calibration_json=?,calibration_revision=? WHERE id=?",
          (json.dumps(calibration), revision, body.source_id))
    return get_calibration(calibration_id)
