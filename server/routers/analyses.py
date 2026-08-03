"""Unified saved analyses: a saved data question (subject + measures + filters +
grouping), not a chart definition. Replaces the legacy block+dataset+params
insight model (server/routers/insights.py, kept only for historical rows and
the one-time best-effort migration in server/db.py:_migrate_insights_to_analyses).

Presentation (KPI/line/bar/table/heatmap/flow-matrix/timeline) is chosen by the
frontend from the query result's `shape` (server/routers/analytics_query.py) —
switching renderers never creates a second analysis record for the same question.
"""
import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import db
from .analytics_query import GroupingIn, QueryIn, _validate

router = APIRouter(tags=["analyses"])

VISIBILITIES = {"visible", "hidden"}
CREATORS = {"user", "agent"}
STATUSES = {"draft", "collecting", "validating", "ready", "degraded", "retired"}


class AnalysisIn(BaseModel):
    name: str
    question: str = ""
    subject: str
    measures: list[str]
    filters: dict = {}
    grouping: GroupingIn = GroupingIn()
    default_range: dict = {}
    comparison: dict = {}
    presentation: str = ""
    pinned: bool = False
    sort_order: int = 0
    visibility: str = "visible"
    created_by: str = "user"
    status: str = "ready"


class AnalysisPatch(BaseModel):
    name: str | None = None
    question: str | None = None
    subject: str | None = None
    measures: list[str] | None = None
    filters: dict | None = None
    grouping: GroupingIn | None = None
    default_range: dict | None = None
    comparison: dict | None = None
    presentation: str | None = None
    pinned: bool | None = None
    sort_order: int | None = None
    visibility: str | None = None
    status: str | None = None


def _validate_shape(subject: str, measures: list[str], grouping: GroupingIn,
                    visibility: str | None = None, status: str | None = None,
                    created_by: str | None = None):
    _validate(QueryIn(subject=subject, measures=measures, grouping=grouping))
    if visibility is not None and visibility not in VISIBILITIES:
        raise HTTPException(422, f"visibility must be one of {sorted(VISIBILITIES)}")
    if status is not None and status not in STATUSES:
        raise HTTPException(422, f"status must be one of {sorted(STATUSES)}")
    if created_by is not None and created_by not in CREATORS:
        raise HTTPException(422, f"created_by must be one of {sorted(CREATORS)}")


def serialize(row: dict) -> dict:
    return {
        "id": row["id"], "name": row["name"], "question": row["question"],
        "subject": row["subject"], "measures": db.jload(row["measures_json"], []),
        "filters": db.jload(row["filters_json"], {}), "grouping": db.jload(row["grouping_json"], {}),
        "default_range": db.jload(row["default_range_json"], {}),
        "comparison": db.jload(row["comparison_json"], {}), "presentation": row["presentation"] or "",
        "pinned": bool(row["pinned"]), "sort_order": row["sort_order"], "visibility": row["visibility"],
        "created_by": row["created_by"], "status": row["status"], "query_hash": row["query_hash"],
        "migrated_from_insight_id": row.get("migrated_from_insight_id"),
        "migration_note": row.get("migration_note") or "",
        "created_at": row["created_at"], "updated_at": row["updated_at"],
    }


@router.get("/analyses")
def list_analyses(include_hidden: bool = False, pinned: bool = False):
    where = []
    if not include_hidden:
        where.append("visibility='visible'")
    if pinned:
        where.append("pinned=1")
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    return [serialize(r) for r in db.q(f"SELECT * FROM analyses {clause} ORDER BY sort_order, id")]


@router.post("/analyses", status_code=201)
def create_analysis(body: AnalysisIn):
    _validate_shape(body.subject, body.measures, body.grouping, body.visibility, body.status, body.created_by)
    grouping = body.grouping.model_dump()
    query_hash = db.analysis_hash(body.subject, body.measures, body.filters, grouping)
    duplicate = db.q1("SELECT id, name FROM analyses WHERE query_hash=? AND visibility='visible'", (query_hash,))
    now = db.now()
    aid = db.ex(
        "INSERT INTO analyses (name, question, subject, measures_json, filters_json, grouping_json,"
        " default_range_json, comparison_json, presentation, pinned, sort_order, visibility, created_by,"
        " status, query_hash, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (body.name, body.question, body.subject, json.dumps(body.measures), json.dumps(body.filters),
         json.dumps(grouping), json.dumps(body.default_range), json.dumps(body.comparison),
         body.presentation, int(body.pinned), body.sort_order, body.visibility, body.created_by,
         body.status, query_hash, now, now),
    )
    result = serialize(db.q1("SELECT * FROM analyses WHERE id=?", (aid,)))
    if duplicate:
        result["duplicate_of"] = {"id": duplicate["id"], "name": duplicate["name"]}
    return result


@router.get("/analyses/{analysis_id}")
def get_analysis(analysis_id: int):
    row = db.q1("SELECT * FROM analyses WHERE id=?", (analysis_id,))
    if not row:
        raise HTTPException(404, "analysis not found")
    return serialize(row)


@router.patch("/analyses/{analysis_id}")
def update_analysis(analysis_id: int, body: AnalysisPatch):
    row = db.q1("SELECT * FROM analyses WHERE id=?", (analysis_id,))
    if not row:
        raise HTTPException(404, "analysis not found")
    subject = body.subject or row["subject"]
    measures = body.measures if body.measures is not None else db.jload(row["measures_json"], [])
    grouping = body.grouping if body.grouping is not None else GroupingIn(**db.jload(row["grouping_json"], {}))
    _validate_shape(subject, measures, grouping, body.visibility, body.status)
    sets, args = [], []
    for field, val in (("name", body.name), ("question", body.question), ("subject", body.subject),
                       ("presentation", body.presentation), ("sort_order", body.sort_order),
                       ("visibility", body.visibility), ("status", body.status)):
        if val is not None:
            sets.append(f"{field}=?"); args.append(val)
    if body.measures is not None:
        sets.append("measures_json=?"); args.append(json.dumps(body.measures))
    if body.filters is not None:
        sets.append("filters_json=?"); args.append(json.dumps(body.filters))
    if body.grouping is not None:
        sets.append("grouping_json=?"); args.append(json.dumps(body.grouping.model_dump()))
    if body.default_range is not None:
        sets.append("default_range_json=?"); args.append(json.dumps(body.default_range))
    if body.comparison is not None:
        sets.append("comparison_json=?"); args.append(json.dumps(body.comparison))
    if body.pinned is not None:
        sets.append("pinned=?"); args.append(int(body.pinned))
    if {"measures", "filters", "grouping"} & body.model_fields_set:
        filters = body.filters if body.filters is not None else db.jload(row["filters_json"], {})
        sets.append("query_hash=?")
        args.append(db.analysis_hash(subject, measures, filters, grouping.model_dump()))
    if sets:
        sets.append("updated_at=?"); args.append(db.now())
        db.ex(f"UPDATE analyses SET {', '.join(sets)} WHERE id=?", (*args, analysis_id))
    return serialize(db.q1("SELECT * FROM analyses WHERE id=?", (analysis_id,)))


@router.delete("/analyses/{analysis_id}")
def delete_analysis(analysis_id: int):
    if not db.q1("SELECT id FROM analyses WHERE id=?", (analysis_id,)):
        raise HTTPException(404, "analysis not found")
    db.ex("DELETE FROM analyses WHERE id=?", (analysis_id,))
    return {"deleted": analysis_id}
