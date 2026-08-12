"""Agent-defined dashboards composed from validated saved queries and safe renderers."""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import db
from .queries import execute_saved_query

router = APIRouter(tags=["dashboards"])
PRESENTATIONS = {"number", "timeseries", "bar", "table", "heatmap"}
SHAPE_COMPATIBILITY = {
    "number": {"scalar"}, "timeseries": {"timeseries"},
    "bar": {"categorical", "timeseries"}, "table": {"scalar", "categorical", "timeseries", "heatmap"},
    "heatmap": {"heatmap"},
}


class DashboardIn(BaseModel):
    name: str
    description: str = ""
    created_by: str = "agent"


class DashboardPatch(BaseModel):
    name: str | None = None
    description: str | None = None


class WidgetIn(BaseModel):
    query_id: int
    title: str
    presentation: str
    configuration: dict = Field(default_factory=dict)
    sort_order: int = 0


class WidgetPatch(BaseModel):
    query_id: int | None = None
    title: str | None = None
    presentation: str | None = None
    configuration: dict | None = None
    sort_order: int | None = None


def serialize_widget(row: dict) -> dict:
    return {"id": row["id"], "dashboard_id": row["dashboard_id"], "query_id": row["query_id"],
            "title": row["title"], "presentation": row["presentation"],
            "configuration": db.jload(row["configuration_json"], {}),
            "sort_order": row["sort_order"], "created_at": row["created_at"],
            "updated_at": row["updated_at"]}


def serialize_dashboard(row: dict, include_widgets: bool = True) -> dict:
    result = {"id": row["id"], "name": row["name"], "description": row["description"],
              "created_by": row["created_by"], "created_at": row["created_at"],
              "updated_at": row["updated_at"]}
    if include_widgets:
        result["widgets"] = [serialize_widget(widget) for widget in db.q(
            "SELECT * FROM dashboard_widgets WHERE dashboard_id=? ORDER BY sort_order,id", (row["id"],))]
    return result


def _dashboard(dashboard_id: int) -> dict:
    row = db.q1("SELECT * FROM dashboards WHERE id=?", (dashboard_id,))
    if not row:
        raise HTTPException(404, "dashboard not found")
    return row


def _validate_widget(query_id: int, presentation: str):
    if presentation not in PRESENTATIONS:
        raise HTTPException(422, f"presentation must be one of {sorted(PRESENTATIONS)}")
    if not db.q1("SELECT id FROM analyses WHERE id=?", (query_id,)):
        raise HTTPException(404, "saved query not found")
    result = execute_saved_query(query_id)
    if result["shape"] not in SHAPE_COMPATIBILITY[presentation]:
        raise HTTPException(
            422, f"{presentation} cannot render query result shape {result['shape']}; "
                 f"expected {sorted(SHAPE_COMPATIBILITY[presentation])}")


@router.get("/dashboards")
def list_dashboards():
    return [serialize_dashboard(row) for row in db.q("SELECT * FROM dashboards ORDER BY id")]


@router.post("/dashboards", status_code=201)
def create_dashboard(body: DashboardIn):
    if body.created_by not in {"agent", "user"}:
        raise HTTPException(422, "created_by must be agent or user")
    now = db.now()
    dashboard_id = db.ex(
        "INSERT INTO dashboards (name,description,created_by,created_at,updated_at) VALUES (?,?,?,?,?)",
        (body.name, body.description, body.created_by, now, now))
    return serialize_dashboard(_dashboard(dashboard_id))


@router.get("/dashboards/{dashboard_id}")
def get_dashboard(dashboard_id: int):
    return serialize_dashboard(_dashboard(dashboard_id))


@router.patch("/dashboards/{dashboard_id}")
def update_dashboard(dashboard_id: int, body: DashboardPatch):
    _dashboard(dashboard_id)
    sets, args = [], []
    for field in ("name", "description"):
        value = getattr(body, field)
        if value is not None:
            sets.append(f"{field}=?"); args.append(value)
    if sets:
        sets.append("updated_at=?"); args.append(db.now())
        db.ex(f"UPDATE dashboards SET {', '.join(sets)} WHERE id=?", (*args, dashboard_id))
    return get_dashboard(dashboard_id)


@router.delete("/dashboards/{dashboard_id}")
def delete_dashboard(dashboard_id: int):
    _dashboard(dashboard_id)
    db.ex("DELETE FROM dashboard_widgets WHERE dashboard_id=?", (dashboard_id,))
    db.ex("DELETE FROM dashboards WHERE id=?", (dashboard_id,))
    return {"deleted": dashboard_id, "queries_preserved": True}


@router.post("/dashboards/{dashboard_id}/widgets", status_code=201)
def add_widget(dashboard_id: int, body: WidgetIn):
    _dashboard(dashboard_id); _validate_widget(body.query_id, body.presentation)
    now = db.now()
    widget_id = db.ex(
        "INSERT INTO dashboard_widgets (dashboard_id,query_id,title,presentation,configuration_json,"
        "sort_order,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (dashboard_id, body.query_id, body.title, body.presentation, json.dumps(body.configuration),
         body.sort_order, now, now))
    return serialize_widget(db.q1("SELECT * FROM dashboard_widgets WHERE id=?", (widget_id,)))


@router.patch("/dashboard-widgets/{widget_id}")
def update_widget(widget_id: int, body: WidgetPatch):
    row = db.q1("SELECT * FROM dashboard_widgets WHERE id=?", (widget_id,))
    if not row:
        raise HTTPException(404, "dashboard widget not found")
    query_id = body.query_id if body.query_id is not None else row["query_id"]
    presentation = body.presentation if body.presentation is not None else row["presentation"]
    _validate_widget(query_id, presentation)
    values = {
        "query_id": query_id, "title": body.title if body.title is not None else row["title"],
        "presentation": presentation,
        "configuration_json": (json.dumps(body.configuration) if body.configuration is not None
                               else row["configuration_json"]),
        "sort_order": body.sort_order if body.sort_order is not None else row["sort_order"],
    }
    db.ex("UPDATE dashboard_widgets SET query_id=?,title=?,presentation=?,configuration_json=?,"
          "sort_order=?,updated_at=? WHERE id=?", (*values.values(), db.now(), widget_id))
    return serialize_widget(db.q1("SELECT * FROM dashboard_widgets WHERE id=?", (widget_id,)))


@router.delete("/dashboard-widgets/{widget_id}")
def delete_widget(widget_id: int):
    if not db.q1("SELECT id FROM dashboard_widgets WHERE id=?", (widget_id,)):
        raise HTTPException(404, "dashboard widget not found")
    db.ex("DELETE FROM dashboard_widgets WHERE id=?", (widget_id,))
    return {"deleted": widget_id}
