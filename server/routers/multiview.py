"""Explicit calibrated camera groups and fused current-state inspection."""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import db
from ..services import multiview

router = APIRouter(tags=["multiview"])


class MultiviewGroupIn(BaseModel):
    name: str
    source_ids: list[int]
    enabled: bool = True
    time_tolerance_s: float = 0.75
    spatial_gate_m: float = 1.5
    track_age_s: float = 2.0
    topology: dict = {}
    configuration: dict = {}


class MultiviewGroupPatch(BaseModel):
    name: str | None = None
    source_ids: list[int] | None = None
    enabled: bool | None = None
    time_tolerance_s: float | None = None
    spatial_gate_m: float | None = None
    track_age_s: float | None = None
    topology: dict | None = None
    configuration: dict | None = None


def serialize_group(row: dict) -> dict:
    return {
        "id": row["id"], "name": row["name"],
        "source_ids": db.jload(row["source_ids_json"], []), "enabled": bool(row["enabled"]),
        "algorithm": row["algorithm"], "algorithm_version": row["algorithm_version"],
        "configuration_revision": row["configuration_revision"],
        "time_tolerance_s": row["time_tolerance_s"], "spatial_gate_m": row["spatial_gate_m"],
        "track_age_s": row["track_age_s"], "topology": db.jload(row["topology_json"], {}),
        "configuration": db.jload(row["configuration_json"], {}),
        "created_at": row["created_at"], "updated_at": row["updated_at"],
    }


def _validate_sources(source_ids: list[int]) -> None:
    if len(set(source_ids)) < 2:
        raise HTTPException(422, "a multiview group requires at least two distinct sources")
    placeholders = ",".join("?" for _ in source_ids)
    sources = db.q(
        f"SELECT id,store_id,calibration_json FROM sources WHERE id IN ({placeholders})", source_ids)
    if {row["id"] for row in sources} != set(source_ids):
        raise HTTPException(404, "one or more sources do not exist")
    if len({row["store_id"] for row in sources}) != 1:
        raise HTTPException(422, "all multiview sources must belong to one mapped space")
    if any(not db.jload(row["calibration_json"], {}).get("H") for row in sources):
        raise HTTPException(409, "every multiview source must have a floor/world calibration")
    rich = db.q(
        f"SELECT source_id,units,world_frame_json FROM camera_calibrations "
        f"WHERE source_id IN ({placeholders})", source_ids)
    if rich:
        if len(rich) != len(source_ids):
            raise HTTPException(409, "do not mix rich and planar-only calibrations in one group")
        signatures = {(row["units"], json.dumps(db.jload(row["world_frame_json"], {}), sort_keys=True))
                      for row in rich}
        if len(signatures) != 1:
            raise HTTPException(422, "rich calibrations use incompatible world frames")


def _validate_tuning(time_tolerance_s: float, spatial_gate_m: float, track_age_s: float):
    if time_tolerance_s <= 0 or spatial_gate_m <= 0 or track_age_s <= 0:
        raise HTTPException(422, "time tolerance, spatial gate, and track age must be positive")


@router.get("/multiview/groups")
def list_groups():
    return [serialize_group(row) for row in db.q("SELECT * FROM multiview_groups ORDER BY id")]


@router.post("/multiview/groups", status_code=201)
def create_group(body: MultiviewGroupIn):
    _validate_sources(body.source_ids)
    _validate_tuning(body.time_tolerance_s, body.spatial_gate_m, body.track_age_s)
    now = db.now()
    group_id = db.ex(
        "INSERT INTO multiview_groups (name,source_ids_json,enabled,time_tolerance_s,spatial_gate_m,"
        "track_age_s,topology_json,configuration_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (body.name, json.dumps(sorted(set(body.source_ids))), int(body.enabled), body.time_tolerance_s,
         body.spatial_gate_m, body.track_age_s, json.dumps(body.topology),
         json.dumps(body.configuration), now, now),
    )
    return serialize_group(db.q1("SELECT * FROM multiview_groups WHERE id=?", (group_id,)))


@router.patch("/multiview/groups/{group_id}")
def update_group(group_id: int, body: MultiviewGroupPatch):
    row = db.q1("SELECT * FROM multiview_groups WHERE id=?", (group_id,))
    if not row:
        raise HTTPException(404, "multiview group not found")
    source_ids = body.source_ids if body.source_ids is not None else db.jload(row["source_ids_json"], [])
    time_tolerance = body.time_tolerance_s if body.time_tolerance_s is not None else row["time_tolerance_s"]
    spatial_gate = body.spatial_gate_m if body.spatial_gate_m is not None else row["spatial_gate_m"]
    track_age = body.track_age_s if body.track_age_s is not None else row["track_age_s"]
    _validate_sources(source_ids); _validate_tuning(time_tolerance, spatial_gate, track_age)
    values = {
        "name": body.name if body.name is not None else row["name"],
        "source_ids_json": json.dumps(sorted(set(source_ids))),
        "enabled": int(body.enabled) if body.enabled is not None else row["enabled"],
        "time_tolerance_s": time_tolerance, "spatial_gate_m": spatial_gate, "track_age_s": track_age,
        "topology_json": json.dumps(body.topology) if body.topology is not None else row["topology_json"],
        "configuration_json": (json.dumps(body.configuration) if body.configuration is not None
                               else row["configuration_json"]),
    }
    db.ex(
        "UPDATE multiview_groups SET name=?,source_ids_json=?,enabled=?,time_tolerance_s=?,"
        "spatial_gate_m=?,track_age_s=?,topology_json=?,configuration_json=?,"
        "configuration_revision=configuration_revision+1,updated_at=? WHERE id=?",
        (*values.values(), db.now(), group_id),
    )
    return serialize_group(db.q1("SELECT * FROM multiview_groups WHERE id=?", (group_id,)))


@router.get("/multiview/current")
def current_entities(group_id: int | None = None, entity_type: str = "person",
                     zone_id: int | None = None):
    multiview.refresh_freshness()
    where, args = ["entity_type=?"], [entity_type]
    if group_id is not None:
        where.append("group_id=?"); args.append(group_id)
    if zone_id is not None:
        where.append("zone_id=?"); args.append(zone_id)
    rows = db.q(
        f"SELECT * FROM fused_current_entities WHERE {' AND '.join(where)} ORDER BY fused_entity_id", args)
    now = db.now()
    group_where, group_args = ("WHERE id=?", (group_id,)) if group_id is not None else ("WHERE enabled=1", ())
    group_status = []
    for group in db.q(f"SELECT * FROM multiview_groups {group_where} ORDER BY id", group_args):
        source_ids = db.jload(group["source_ids_json"], [])
        placeholders = ",".join("?" for _ in source_ids)
        samples = db.q(
            f"SELECT source_id,ts FROM source_current_samples WHERE entity_type=? "
            f"AND source_id IN ({placeholders})", (entity_type, *source_ids)) if source_ids else []
        fresh = [sample["source_id"] for sample in samples if now - sample["ts"] <= group["track_age_s"]]
        quality = "known" if len(fresh) == len(source_ids) else ("partial" if fresh else "unknown")
        group_status.append({"id": group["id"], "name": group["name"], "quality": quality,
                             "fresh_source_ids": fresh, "source_ids": source_ids})
    return {
        "mode": "fused", "as_of": now, "groups": group_status,
        "entities": [{
            "fused_entity_id": row["fused_entity_id"], "group_id": row["group_id"],
            "entity_type": row["entity_type"], "timestamp": row["ts"],
            "point_map": {"x": row["x_map"], "y": row["y_map"]}, "zone_id": row["zone_id"],
            "confidence": row["confidence"], "quality": row["quality"],
            "freshness_s": max(0.0, now - row["ts"]),
            "members": db.jload(row["member_evidence_json"], []),
        } for row in rows],
    }


@router.get("/multiview/occupancy")
def current_occupancy(group_id: int, zone_id: int, entity_type: str = "person"):
    multiview.refresh_freshness()
    row = db.q1(
        "SELECT * FROM zone_current_occupancy WHERE group_id=? AND zone_id=? AND entity_type=?",
        (group_id, zone_id, entity_type),
    )
    if not row:
        return {"group_id": group_id, "zone_id": zone_id, "entity_type": entity_type,
                "value": None, "quality": "unknown", "as_of": db.now(), "provenance": {}}
    result = dict(row)
    result["provenance"] = db.jload(result.pop("provenance_json"), {})
    return result
