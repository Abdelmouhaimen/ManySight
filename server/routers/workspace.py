"""Explicit workspace reset operations and space-revision provenance."""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import db
from ..services import camera_reset, config_cache, realtime

router = APIRouter(tags=["workspace"])


class SpaceResetIn(BaseModel):
    confirmation: str
    history: str = "keep"  # keep | delete


class ObservationResetIn(BaseModel):
    confirmation: str


class CameraResetIn(BaseModel):
    # Preview by default. This is the only reset that removes the cameras
    # themselves, so the impact has to be seeable before it is possible.
    dry_run: bool = True
    confirmation: str = ""
    # Optional guard carried from a preview: if the camera set changed since
    # then, the reset is refused instead of removing something the user never
    # saw listed.
    reset_token: str | None = None


def _snapshot(con) -> dict:
    def rows(table: str) -> list[dict]:
        return [dict(row) for row in con.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()]

    return {
        "store": dict(con.execute("SELECT * FROM stores WHERE id=1").fetchone()),
        "zones": rows("zones"),
        "sources": rows("sources"),
        "projection_surfaces": rows("projection_surfaces"),
        "zone_views": rows("zone_views"),
        "camera_calibrations": rows("camera_calibrations"),
        "multiview_groups": rows("multiview_groups"),
    }


def _clear_materialized(con, include_history: bool = True) -> None:
    for table in ("source_current_entities", "source_current_samples", "fused_current_entities",
                  "zone_current_occupancy"):
        con.execute(f"DELETE FROM {table}")
    con.execute("UPDATE fused_entities SET ended_at=COALESCE(ended_at,?)", (db.now(),))
    if include_history:
        for table in ("fused_entity_members", "fused_observations", "fused_entities",
                      "zone_occupancy_observations"):
            con.execute(f"DELETE FROM {table}")


def _clear_observations(con) -> None:
    _clear_materialized(con)
    con.execute("DELETE FROM events")
    con.execute("DELETE FROM alerts")
    con.execute("UPDATE alert_rules SET last_fired_at=NULL,condition_state_json='{}'")
    con.execute("UPDATE sources SET event_count=0,last_observation_at=NULL,last_ingestion_at=NULL")
    con.execute("UPDATE jobs SET event_count=0,last_event_at=NULL")


@router.get("/workspace/revisions", summary="List mapped-space revisions")
def list_space_revisions():
    current = db.current_space_revision_id()
    return [{
        "id": row["id"], "revision_number": row["revision_number"],
        "status": row["status"], "reason": row["reason"],
        "created_at": row["created_at"], "current": row["id"] == current,
    } for row in db.q("SELECT * FROM space_revisions WHERE store_id=1 ORDER BY revision_number DESC")]


@router.post(
    "/workspace/reinitialize-space",
    summary="Start a new mapped-space revision",
    description="Requires exact confirmation. Keeps protected source credentials and optionally archives observation history.",
)
def reinitialize_space(body: SpaceResetIn):
    if body.confirmation != "REINITIALIZE SPACE":
        raise HTTPException(422, "type REINITIALIZE SPACE to confirm")
    if body.history not in {"keep", "delete"}:
        raise HTTPException(422, "history must be keep or delete")
    con = db.connect()
    try:
        con.execute("BEGIN IMMEDIATE")
        store = dict(con.execute("SELECT * FROM stores WHERE id=1").fetchone())
        old_revision_id = int(store.get("current_space_revision_id") or 1)
        old_revision = con.execute(
            "SELECT revision_number FROM space_revisions WHERE id=?", (old_revision_id,)
        ).fetchone()
        number = int(old_revision["revision_number"] if old_revision else 1) + 1
        con.execute(
            "UPDATE space_revisions SET status='previous',snapshot_json=? WHERE id=?",
            (json.dumps(_snapshot(con), sort_keys=True), old_revision_id),
        )
        cursor = con.execute(
            "INSERT INTO space_revisions (store_id,revision_number,status,reason,snapshot_json,created_at) "
            "VALUES (1,?,'current','space_reinitialized','{}',?)",
            (number, db.now()),
        )
        new_revision_id = cursor.lastrowid
        _clear_materialized(con, include_history=body.history == "delete")
        for table in ("zone_geometry_provenance", "zone_views", "projection_surfaces",
                      "camera_calibrations", "multiview_groups", "zones"):
            con.execute(f"DELETE FROM {table}")
        con.execute(
            "UPDATE sources SET map_x=NULL,map_y=NULL,calibration_json=NULL,"
            "calibration_revision=calibration_revision+1,event_count=0,"
            "last_observation_at=NULL,last_ingestion_at=NULL"
        )
        con.execute(
            "UPDATE stores SET name='My space',width_m=20,height_m=12,map_json='{}',"
            "environment='setup',current_space_revision_id=? WHERE id=1",
            (new_revision_id,),
        )
        if body.history == "delete":
            _clear_observations(con)
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
        # This transaction rewrites geometry and groups on a raw connection, so
        # it bypasses the db.ex configuration-cache hook; the live coordinator's
        # in-memory snapshots refer to a space revision that no longer exists.
        config_cache.invalidate("space_reinitialized")
        realtime.coordinator.reset()
    return {
        "reinitialized": True, "history": body.history,
        "previous_space_revision_id": old_revision_id,
        "space_revision_id": new_revision_id,
    }


@router.post(
    "/workspace/reinitialize-observations",
    summary="Clear observation and derived-state history",
    description="Requires exact confirmation. Preserves source, geometry, query, dashboard, and alert-rule configuration.",
)
def reinitialize_observations(body: ObservationResetIn):
    if body.confirmation != "REINITIALIZE OBSERVATIONS":
        raise HTTPException(422, "type REINITIALIZE OBSERVATIONS to confirm")
    con = db.connect()
    try:
        con.execute("BEGIN IMMEDIATE")
        _clear_observations(con)
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
        # Cleared current state must not be resurrected by a pending live tick
        # holding snapshots of samples that no longer exist.
        realtime.coordinator.reset()
    return {"reinitialized": True, "space_revision_id": db.current_space_revision_id()}


@router.post(
    "/workspace/reset-cameras",
    summary="Remove every camera and its camera-specific setup",
    description=(
        "Destructive, and distinct from the two reinitialize operations: this removes the "
        "cameras themselves along with their connections, stored credentials, placement, "
        "calibration, projection surfaces, camera zone views, camera observations and "
        "combined-tracking state. The workspace, floor plan, dimensions and canonical zones "
        "are preserved. Defaults to a dry run that only reports the impact."
    ),
)
def reset_cameras(body: CameraResetIn):
    """Preview by default; execute only on exact confirmation."""
    if camera_reset.in_isolated_demo_workspace():
        raise HTTPException(409, "exit the guided demo before resetting your cameras")
    if body.dry_run:
        return {
            "reset": False, "dry_run": True,
            "confirmation_required": camera_reset.CONFIRMATION,
            "impact": camera_reset.impact(),
            "preserves": ["the workspace and its floor plan", "the space dimensions",
                          "canonical zones (their camera views are removed)",
                          "saved queries, dashboards and alert-rule definitions"],
            "note": ("Alert rules that could no longer fire are disabled, not deleted. Saved "
                     "queries are kept and reported as having stale references."),
        }
    if body.confirmation != camera_reset.CONFIRMATION:
        raise HTTPException(422, f"type {camera_reset.CONFIRMATION} to confirm")
    if body.reset_token and body.reset_token != camera_reset.reset_token():
        raise HTTPException(409, {
            "message": "the camera list changed since this preview was taken",
            "reason": "stale_preview",
            "current_reset_token": camera_reset.reset_token(),
            "next_steps": ["Preview the reset again and confirm against the new impact."],
        })
    return camera_reset.execute()
