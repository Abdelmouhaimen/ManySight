"""Rebuild camera-derived canonical zones after a floor calibration changes.

Camera polygons remain source-pixel evidence. A calibration change alters where
that evidence lands on the metric floor, so zones whose canonical geometry is
still fully controlled by projection provenance must be rebuilt from the stored
pixel contributions. Map-authored or subsequently hand-edited geometry is
deliberately left alone.
"""
from __future__ import annotations

import json

from .. import db
from . import homography, zone_geometry


def _latest_contributions(zone_id: int) -> list[dict]:
    return db.q(
        "SELECT provenance.* FROM zone_geometry_provenance provenance "
        "JOIN (SELECT source_id, MAX(id) AS id FROM zone_geometry_provenance "
        "      WHERE zone_id=? GROUP BY source_id) latest ON latest.id=provenance.id "
        "ORDER BY provenance.source_id",
        (zone_id,),
    )


def _project_contribution(stored: dict) -> tuple[list[dict], dict]:
    pixel_polygon = db.jload(stored["original_pixel_polygon_json"], [])
    source = db.q1(
        "SELECT calibration_json,calibration_revision FROM sources WHERE id=?",
        (stored["source_id"],),
    )
    if not source:
        raise ValueError(f"source {stored['source_id']} no longer exists")

    surface = None
    if stored.get("projection_surface_id") is not None:
        surface = db.q1(
            "SELECT homography_json,revision FROM projection_surfaces WHERE id=?",
            (stored["projection_surface_id"],),
        )
        if not surface:
            raise ValueError(
                f"projection surface {stored['projection_surface_id']} no longer exists"
            )
        matrix = db.jload(surface["homography_json"], None)
    else:
        calibration = db.jload(source.get("calibration_json"), None)
        matrix = (calibration or {}).get("H")
    if not matrix:
        raise ValueError(f"source {stored['source_id']} has no usable floor calibration")

    projected = homography.project(matrix, pixel_polygon)
    points = [{"x": float(x), "y": float(y)} for x, y in projected]
    zone_geometry.polygon_from_points(points)
    return points, {
        "source_calibration_revision": source["calibration_revision"],
        "projection_surface_revision": surface["revision"] if surface else None,
    }


def refresh_camera_derived_zones(source_id: int) -> list[dict]:
    """Reproject zones affected by ``source_id`` using current calibrations.

    A zone is eligible only when its first contribution created it from camera
    pixels and its current revision is still the latest provenance-controlled
    revision. This prevents a calibration save from overwriting a canonical
    zone created on the map or manually edited afterward.

    The caller should run this inside the same database transaction as the
    calibration update so either the calibration and every eligible zone change
    together, or none of them do.
    """
    candidates = db.q(
        "SELECT DISTINCT z.* FROM zones z "
        "JOIN zone_geometry_provenance p ON p.zone_id=z.id "
        "WHERE p.source_id=? ORDER BY z.id",
        (source_id,),
    )
    refreshed: list[dict] = []
    for zone in candidates:
        first = db.q1(
            "SELECT operation FROM zone_geometry_provenance WHERE zone_id=? ORDER BY id LIMIT 1",
            (zone["id"],),
        )
        latest_revision = db.q1(
            "SELECT MAX(resulting_zone_revision) AS revision "
            "FROM zone_geometry_provenance WHERE zone_id=?",
            (zone["id"],),
        )
        if (
            not first
            or first["operation"] != "create_from_camera_polygon"
            or int((latest_revision or {}).get("revision") or 0) != int(zone["revision"])
        ):
            continue

        projected: list[tuple[dict, list[dict], dict]] = []
        combined = None
        for stored in _latest_contributions(zone["id"]):
            points, metadata = _project_contribution(stored)
            combined = (
                zone_geometry.as_geojson(zone_geometry.polygon_from_points(points))
                if combined is None
                else zone_geometry.as_geojson(zone_geometry.union(combined, points))
            )
            projected.append((stored, points, metadata))

        if combined is None:
            continue
        next_revision = int(zone["revision"]) + 1
        now = db.now()
        db.ex(
            "UPDATE zones SET geometry_json=?,polygon_json=?,revision=?,updated_at=? WHERE id=?",
            (json.dumps(combined), json.dumps(zone_geometry.legacy_exterior(combined)),
             next_revision, now, zone["id"]),
        )
        for stored, points, metadata in projected:
            db.ex(
                "INSERT INTO zone_geometry_provenance "
                "(zone_id,source_id,source_calibration_revision,zone_view_id,zone_view_revision,"
                "projection_surface_id,projection_surface_revision,original_pixel_polygon_json,"
                "projected_map_polygon_json,operation,resulting_zone_revision,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (zone["id"], stored["source_id"], metadata["source_calibration_revision"],
                 stored.get("zone_view_id"), stored.get("zone_view_revision"),
                 stored.get("projection_surface_id"), metadata["projection_surface_revision"],
                 stored["original_pixel_polygon_json"], json.dumps(points),
                 "refresh_after_calibration", next_revision, now),
            )
        refreshed.append({"zone_id": zone["id"], "revision": next_revision})
    return refreshed
