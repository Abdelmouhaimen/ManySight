"""Insight definitions: the user-facing catalogue rendered by the Insights tab.

An insight is a structured definition — a question, a visualization block, and a
platform dataset with query params — never arbitrary UI code. Users register them
from templates in the dashboard; agents register them over MCP after posting
observations. The dashboard renders each definition from its block registry and
always shows the stated limitations.
"""
import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import db

router = APIRouter(tags=["insights"])

BLOCKS = {"metric", "line", "bar", "table", "heatmap_map", "flow_matrix", "state_timeline"}
DATASETS = {"summary", "heatmap", "dwell", "occupancy", "counts", "transitions", "states"}
BLOCK_DATASETS = {  # which platform dataset(s) each block can render
    "metric": {"summary", "dwell", "occupancy"},
    "line": {"occupancy", "counts"},
    "bar": {"dwell"},
    "table": {"dwell", "transitions"},
    "heatmap_map": {"heatmap"},
    "flow_matrix": {"transitions"},
    "state_timeline": {"states"},
}
STATUSES = {"draft", "collecting", "validating", "ready", "degraded", "retired"}
VISIBILITIES = {"visible", "hidden"}
CREATORS = {"user", "agent"}


class InsightIn(BaseModel):
    title: str
    question: str = ""
    block: str
    dataset: str
    params: dict = {}
    unit: str = ""
    limitations: str = ""
    pinned: bool = False
    sort_order: int = 0
    visibility: str = "visible"
    created_by: str = "user"
    status: str = "ready"


class InsightPatch(BaseModel):
    title: str | None = None
    question: str | None = None
    block: str | None = None
    dataset: str | None = None
    params: dict | None = None
    unit: str | None = None
    limitations: str | None = None
    pinned: bool | None = None
    sort_order: int | None = None
    visibility: str | None = None
    status: str | None = None


def _validate(block: str, dataset: str, visibility: str | None = None,
              status: str | None = None, created_by: str | None = None):
    if block not in BLOCKS:
        raise HTTPException(422, f"block must be one of {sorted(BLOCKS)}")
    if dataset not in DATASETS:
        raise HTTPException(422, f"dataset must be one of {sorted(DATASETS)}")
    if dataset not in BLOCK_DATASETS[block]:
        raise HTTPException(422, f"block '{block}' renders {sorted(BLOCK_DATASETS[block])}, not '{dataset}'")
    if visibility is not None and visibility not in VISIBILITIES:
        raise HTTPException(422, f"visibility must be one of {sorted(VISIBILITIES)}")
    if status is not None and status not in STATUSES:
        raise HTTPException(422, f"status must be one of {sorted(STATUSES)}")
    if created_by is not None and created_by not in CREATORS:
        raise HTTPException(422, f"created_by must be one of {sorted(CREATORS)}")


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


@router.post("/insights", status_code=201)
def create_insight(body: InsightIn):
    _validate(body.block, body.dataset, body.visibility, body.status, body.created_by)
    iid = db.ex(
        "INSERT INTO insight_definitions (title, question, block, dataset, params_json, unit,"
        " limitations, pinned, sort_order, visibility, created_by, status, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (body.title, body.question, body.block, body.dataset, json.dumps(body.params), body.unit,
         body.limitations, int(body.pinned), body.sort_order, body.visibility, body.created_by,
         body.status, db.now(), db.now()),
    )
    return serialize(db.q1("SELECT * FROM insight_definitions WHERE id=?", (iid,)))


@router.get("/insights/templates")
def list_templates():
    """Template catalog for the "Add insight" picker, assembled from the data that is
    actually present. Unavailable templates are returned with a `requires` hint."""
    zones = db.q("SELECT id, name, ztype FROM zones ORDER BY id")
    has_positions = bool(db.q1("SELECT 1 FROM events WHERE x_map IS NOT NULL LIMIT 1"))
    has_zone_flow = bool(db.q1("SELECT 1 FROM events WHERE event_type='zone_enter' LIMIT 1"))
    detection_labels = [r["label"] for r in db.q(
        "SELECT DISTINCT label FROM events WHERE event_type='detection'"
        " AND track_id IS NOT NULL AND label IS NOT NULL AND label!='' ORDER BY label")]
    has_labelled_occupancy = bool(db.q1(
        "SELECT 1 FROM events WHERE event_type='detection' AND track_id IS NOT NULL"
        " AND zone_id IS NOT NULL AND label IS NOT NULL AND label!='' LIMIT 1"))
    count_labels = [r["label"] for r in db.q(
        "SELECT DISTINCT label FROM events WHERE event_type='count'"
        " AND label IS NOT NULL AND label!='' ORDER BY label")]
    state_sources = {r["source_id"] for r in db.q(
        "SELECT DISTINCT source_id FROM events WHERE event_type='state_change'"
        " AND source_id IS NOT NULL")}
    src_names = {s["id"]: s["name"] for s in db.q("SELECT id, name FROM sources")}
    attr_keys = sorted({k for r in db.q(
        "SELECT attributes FROM events WHERE event_type='zone_enter'"
        " AND attributes!='{}' ORDER BY id DESC LIMIT 1000")
        for k in db.jload(r["attributes"], {})})

    templates = [
        {"key": "occupancy_line", "title": "Presence over time", "block": "line", "dataset": "occupancy",
         "question": "How many tracked objects are present over time?",
         "params": {"event_type": "detection"}, "unit": "objects",
         "limitations": "Distinct track IDs per interval — re-identified people count twice.",
         "available": True, "requires": ""},
        {"key": "heatmap_map", "title": "Activity heatmap", "block": "heatmap_map", "dataset": "heatmap",
         "question": "Where does activity concentrate on the floor?", "params": {}, "unit": "",
         "limitations": "Needs calibrated cameras; uncalibrated detections are not shown.",
         "available": has_positions, "requires": "" if has_positions else "position events from a calibrated camera"},
        {"key": "dwell_bar", "title": "Dwell by zone", "block": "bar", "dataset": "dwell",
         "question": "How long do visitors stay in each zone?", "params": {}, "unit": "seconds",
         "limitations": "Derived from enter/exit pairs; in-progress visits are clipped to the window.",
         "available": has_zone_flow, "requires": "" if has_zone_flow else "zone_enter/zone_exit events"},
        {"key": "flow_matrix", "title": "Zone-to-zone flow", "block": "flow_matrix", "dataset": "transitions",
         "question": "How do people move between zones?", "params": {}, "unit": "moves",
         "limitations": "Counts consecutive zone entries per track; gaps over 30 minutes break a path.",
         "available": has_zone_flow, "requires": "" if has_zone_flow else "zone_enter events"},
        {"key": "visits_metric", "title": "Tracked visits", "block": "metric", "dataset": "summary",
         "question": "How many distinct visitors were tracked?", "params": {"field": "tracks"}, "unit": "visits",
         "limitations": "Track IDs are per-worker-run, not persistent identities.",
         "available": True, "requires": ""},
    ]
    templates.insert(1, {
        "key": "detection_classes_line", "title": "Detection classes over time",
        "block": "line", "dataset": "occupancy",
        "question": "How do detected classes compare over time?",
        "params": {"event_type": "detection", "group_by": "label"}, "unit": "objects",
        "limitations": "Counts distinct worker track IDs per class and interval; class and tracking accuracy depend on the model.",
        "available": has_labelled_occupancy,
        "requires": "" if has_labelled_occupancy else
                    "labelled, zone-assigned detection events with stable track IDs"})
    if not count_labels:
        templates.insert(1, {
            "key": "counts_line_unavailable", "title": "Counts over time",
            "block": "line", "dataset": "counts",
            "question": "How does a measured population change over time?",
            "params": {}, "unit": "objects",
            "limitations": "Averages worker-reported count samples per interval; accuracy depends on the model.",
            "available": False, "requires": "labelled count events with a numeric value"})
    for z in zones:
        if z["ztype"] in {"checkout", "queue"}:
            templates.append({
                "key": f"queue_dwell_{z['id']}", "title": f"Queue presence — {z['name']}",
                "block": "metric", "dataset": "dwell",
                "question": f"How long do people spend in {z['name']}?",
                "params": {"zone_id": z["id"]}, "unit": "seconds",
                "limitations": "Derived dwell in the zone, not validated wait time.",
                "available": has_zone_flow, "requires": "" if has_zone_flow else "zone_enter/zone_exit events"})
    for label in count_labels:
        templates.append({
            "key": f"count_line_{label}", "title": f"Population — {label}",
            "block": "line", "dataset": "counts",
            "question": f"How many {label} are visible over time?",
            "params": {"label": label}, "unit": label,
            "limitations": "Averages per-frame model counts per interval; accuracy depends on the model.",
            "available": True, "requires": ""})
    for sid in sorted(state_sources):
        templates.append({
            "key": f"states_{sid}", "title": f"State timeline — {src_names.get(sid, f'source {sid}')}",
            "block": "state_timeline", "dataset": "states",
            "question": "What state has this equipment been in, and for how long?",
            "params": {"source_id": sid}, "unit": "",
            "limitations": "Durations derived from state_change timestamps; gaps read as the last known state.",
            "available": True, "requires": ""})
    for key in attr_keys:
        templates.append({
            "key": f"dwell_by_{key}", "title": f"Dwell by {key}",
            "block": "bar", "dataset": "dwell",
            "question": f"How does dwell time differ by {key}?",
            "params": {"group_by": key}, "unit": "seconds",
            "limitations": f"Groups derived dwell by the worker-reported '{key}' attribute; accuracy depends on the model.",
            "available": True, "requires": ""})
    return {"templates": templates,
            "parameters": {"detection_labels": detection_labels, "count_labels": count_labels}}


@router.get("/insights/{insight_id}")
def get_insight(insight_id: int):
    row = db.q1("SELECT * FROM insight_definitions WHERE id=?", (insight_id,))
    if not row:
        raise HTTPException(404, "insight not found")
    return serialize(row)


@router.put("/insights/{insight_id}")
def update_insight(insight_id: int, body: InsightPatch):
    row = db.q1("SELECT * FROM insight_definitions WHERE id=?", (insight_id,))
    if not row:
        raise HTTPException(404, "insight not found")
    _validate(body.block or row["block"], body.dataset or row["dataset"],
              body.visibility, body.status)
    sets, args = [], []
    for field, val in (("title", body.title), ("question", body.question), ("block", body.block),
                       ("dataset", body.dataset), ("unit", body.unit),
                       ("limitations", body.limitations), ("sort_order", body.sort_order),
                       ("visibility", body.visibility), ("status", body.status)):
        if val is not None:
            sets.append(f"{field}=?"); args.append(val)
    if body.params is not None:
        sets.append("params_json=?"); args.append(json.dumps(body.params))
    if body.pinned is not None:
        sets.append("pinned=?"); args.append(int(body.pinned))
    if sets:
        sets.append("updated_at=?"); args.append(db.now())
        db.ex(f"UPDATE insight_definitions SET {', '.join(sets)} WHERE id=?", (*args, insight_id))
    return serialize(db.q1("SELECT * FROM insight_definitions WHERE id=?", (insight_id,)))


@router.delete("/insights/{insight_id}")
def delete_insight(insight_id: int):
    if not db.q1("SELECT id FROM insight_definitions WHERE id=?", (insight_id,)):
        raise HTTPException(404, "insight not found")
    db.ex("DELETE FROM insight_definitions WHERE id=?", (insight_id,))
    return {"deleted": insight_id}
