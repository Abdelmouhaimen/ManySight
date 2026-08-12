"""Public saved-query terminology over the deterministic analytics engine."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import db
from . import analyses, analytics_query

router = APIRouter(tags=["queries"])


@router.get("/queries/capabilities")
def query_capabilities():
    return analytics_query.capabilities()


@router.get("/queries")
def list_saved_queries(include_hidden: bool = False):
    return analyses.list_analyses(include_hidden=include_hidden)


@router.post("/queries", status_code=201)
def create_saved_query(body: analyses.AnalysisIn):
    return analyses.create_analysis(body)


@router.get("/queries/{query_id}")
def get_saved_query(query_id: int):
    return analyses.get_analysis(query_id)


@router.patch("/queries/{query_id}")
def update_saved_query(query_id: int, body: analyses.AnalysisPatch):
    return analyses.update_analysis(query_id, body)


@router.delete("/queries/{query_id}")
def delete_saved_query(query_id: int):
    referenced = db.q1("SELECT id FROM dashboard_widgets WHERE query_id=? LIMIT 1", (query_id,))
    if referenced:
        raise HTTPException(409, "query is referenced by a dashboard widget")
    return analyses.delete_analysis(query_id)


@router.post("/queries/{query_id}/execute")
def execute_saved_query(query_id: int):
    definition = analyses.get_analysis(query_id)
    query = analytics_query.QueryIn(
        subject=definition["subject"], measures=definition["measures"],
        filters=definition["filters"], grouping=definition["grouping"],
        range=definition["default_range"], comparison=definition["comparison"],
    )
    return analytics_query.query_analytics(query)
