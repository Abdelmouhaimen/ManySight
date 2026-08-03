"""Historical insight definitions — READ-ONLY legacy surface.

The block+dataset+params insight model is retired in favor of unified saved
analyses (server/routers/analyses.py, `analyses` table). Every row here was
best-effort migrated into `analyses` once at startup
(server/db.py:_migrate_insights_to_analyses) — use `list_analyses`/`create_analysis`
for all current work. This router keeps only read access (and delete, for
cleanup) to historical rows; there is no create/update path and no
`BLOCK_DATASETS` compatibility concept anymore — creating a NEW insight card in
the old shape is exactly what the redesign removed.
"""
from fastapi import APIRouter, HTTPException

from .. import db

router = APIRouter(tags=["insights"])


def serialize(row: dict, zone_names: dict[int, str] | None = None) -> dict:
    params = db.jload(row["params_json"], {})
    zone_names = zone_names if zone_names is not None else {z["id"]: z["name"] for z in db.q("SELECT id, name FROM zones")}
    return {"id": row["id"], "title": row["title"], "question": row["question"],
            "block": row["block"], "dataset": row["dataset"], "params": params,
            "unit": row["unit"], "limitations": row["limitations"],
            "pinned": bool(row["pinned"]), "sort_order": row["sort_order"],
            "visibility": row["visibility"], "created_by": row["created_by"],
            "status": row["status"], "zone_name": zone_names.get(params.get("zone_id")),
            "created_at": row["created_at"], "updated_at": row["updated_at"]}


@router.get("/insights")
def list_insights(include_hidden: bool = False, pinned: bool = False):
    where = []
    if not include_hidden:
        where.append("visibility='visible'")
    if pinned:
        where.append("pinned=1")
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    zone_names = {z["id"]: z["name"] for z in db.q("SELECT id, name FROM zones")}
    return [serialize(r, zone_names)
            for r in db.q(f"SELECT * FROM insight_definitions {clause} ORDER BY sort_order, id")]


@router.get("/insights/{insight_id}")
def get_insight(insight_id: int):
    row = db.q1("SELECT * FROM insight_definitions WHERE id=?", (insight_id,))
    if not row:
        raise HTTPException(404, "insight not found")
    return serialize(row)


@router.delete("/insights/{insight_id}")
def delete_insight(insight_id: int):
    if not db.q1("SELECT id FROM insight_definitions WHERE id=?", (insight_id,)):
        raise HTTPException(404, "insight not found")
    db.ex("DELETE FROM insight_definitions WHERE id=?", (insight_id,))
    return {"deleted": insight_id}
