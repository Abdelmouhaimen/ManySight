"""Alert rules (the 'if this then that' layer) and the fired-alert log."""
import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import db

router = APIRouter(tags=["alerts"])

RULE_KINDS = {"dwell_exceeds", "occupancy_exceeds", "state_alert", "event_match",
              "analysis_condition", "query_condition"}
REVIEW_STATUSES = {"new", "in_review", "resolved", "dismissed"}


class RuleIn(BaseModel):
    name: str
    kind: str
    params: dict = {}
    # `analysis_condition` rules use these instead of `params`: analysis is
    # {subject, measures, filters}; condition is {operator, value, for_seconds,
    # window_s}. Evaluated periodically (services/alert_engine.py:evaluate_ongoing),
    # not just on ingestion, so it doesn't need a new observation to fire.
    analysis: dict | None = None
    condition: dict | None = None
    webhook_url: str = ""
    cooldown_s: float = 60
    enabled: bool = True


class RulePatch(BaseModel):
    name: str | None = None
    kind: str | None = None
    params: dict | None = None
    analysis: dict | None = None
    condition: dict | None = None
    webhook_url: str | None = None
    cooldown_s: float | None = None
    enabled: bool | None = None


class AlertPatch(BaseModel):
    status: str | None = None
    note: str | None = None


def serialize(row: dict) -> dict:
    return {"id": row["id"], "name": row["name"], "kind": row["kind"],
            "params": db.jload(row["params_json"], {}),
            "analysis": db.jload(row.get("analysis_json"), None),
            "condition": db.jload(row.get("condition_json"), None),
            "webhook_url": row["webhook_url"],
            "cooldown_s": row["cooldown_s"], "enabled": bool(row["enabled"]),
            "last_fired_at": row["last_fired_at"], "created_at": row["created_at"]}


def serialize_alert(row: dict, rules: dict[int, str] | None = None) -> dict:
    rules = rules or {r["id"]: r["name"] for r in db.q("SELECT id, name FROM alert_rules")}
    return {"id": row["id"], "rule_id": row["rule_id"], "rule_name": rules.get(row["rule_id"]),
            "ts": row["ts"], "title": row["title"], "message": row["message"],
            "payload": db.jload(row["payload_json"], {}), "acknowledged": bool(row["acknowledged"]),
            "status": row.get("status") or ("resolved" if row["acknowledged"] else "new"),
            "note": row.get("note") or "", "resolved_at": row.get("resolved_at")}


@router.get("/alert-rules")
def list_rules():
    return [serialize(r) for r in db.q("SELECT * FROM alert_rules ORDER BY id")]


@router.post("/alert-rules", status_code=201)
def create_rule(body: RuleIn):
    if body.kind not in RULE_KINDS:
        raise HTTPException(422, f"kind must be one of {sorted(RULE_KINDS)}")
    if body.kind == "query_condition":
        query_id = body.params.get("query_id")
        if not query_id or not db.q1("SELECT id FROM analyses WHERE id=?", (query_id,)):
            raise HTTPException(422, "query_condition requires params.query_id for an existing saved query")
        if body.condition is None:
            raise HTTPException(422, "query_condition requires a condition")
    rid = db.ex(
        "INSERT INTO alert_rules (name,kind,params_json,analysis_json,condition_json,webhook_url,"
        "cooldown_s,enabled,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (body.name, body.kind, json.dumps(body.params), json.dumps(body.analysis) if body.analysis else None,
         json.dumps(body.condition) if body.condition else None, body.webhook_url,
         body.cooldown_s, int(body.enabled), db.now()),
    )
    return serialize(db.q1("SELECT * FROM alert_rules WHERE id=?", (rid,)))


@router.put("/alert-rules/{rule_id}")
def update_rule(rule_id: int, body: RulePatch):
    if not db.q1("SELECT id FROM alert_rules WHERE id=?", (rule_id,)):
        raise HTTPException(404, "rule not found")
    if body.kind is not None and body.kind not in RULE_KINDS:
        raise HTTPException(422, f"kind must be one of {sorted(RULE_KINDS)}")
    sets, args = [], []
    for field, val in (("name", body.name), ("kind", body.kind), ("webhook_url", body.webhook_url),
                       ("cooldown_s", body.cooldown_s)):
        if val is not None:
            sets.append(f"{field}=?"); args.append(val)
    if body.params is not None:
        sets.append("params_json=?"); args.append(json.dumps(body.params))
    if body.analysis is not None:
        sets.append("analysis_json=?"); args.append(json.dumps(body.analysis))
    if body.condition is not None:
        sets.append("condition_json=?"); args.append(json.dumps(body.condition))
    if body.enabled is not None:
        sets.append("enabled=?"); args.append(int(body.enabled))
    if sets:
        db.ex(f"UPDATE alert_rules SET {', '.join(sets)} WHERE id=?", (*args, rule_id))
    return serialize(db.q1("SELECT * FROM alert_rules WHERE id=?", (rule_id,)))


@router.delete("/alert-rules/{rule_id}")
def delete_rule(rule_id: int):
    if not db.q1("SELECT id FROM alert_rules WHERE id=?", (rule_id,)):
        raise HTTPException(404, "rule not found")
    db.ex("DELETE FROM alert_rules WHERE id=?", (rule_id,))
    return {"deleted": rule_id}


@router.get("/alerts")
def list_alerts(limit: int = 100, unacked: bool = False):
    where = "WHERE acknowledged=0" if unacked else ""
    rows = db.q(f"SELECT * FROM alerts {where} ORDER BY ts DESC LIMIT {min(max(limit, 1), 1000)}")
    rules = {r["id"]: r["name"] for r in db.q("SELECT id, name FROM alert_rules")}
    return [serialize_alert(r, rules) for r in rows]


@router.put("/alerts/{alert_id}")
def update_alert(alert_id: int, body: AlertPatch):
    row = db.q1("SELECT * FROM alerts WHERE id=?", (alert_id,))
    if not row:
        raise HTTPException(404, "alert not found")
    sets, args = [], []
    if body.status is not None:
        if body.status not in REVIEW_STATUSES:
            raise HTTPException(422, f"status must be one of {sorted(REVIEW_STATUSES)}")
        sets.extend(["status=?", "acknowledged=?", "resolved_at=?"])
        args.extend([body.status, int(body.status != "new"), db.now() if body.status in {"resolved", "dismissed"} else None])
    if body.note is not None:
        sets.append("note=?"); args.append(body.note)
    if sets:
        db.ex(f"UPDATE alerts SET {', '.join(sets)} WHERE id=?", (*args, alert_id))
    return serialize_alert(db.q1("SELECT * FROM alerts WHERE id=?", (alert_id,)))


@router.post("/alerts/{alert_id}/ack")
def ack_alert(alert_id: int):
    if not db.q1("SELECT id FROM alerts WHERE id=?", (alert_id,)):
        raise HTTPException(404, "alert not found")
    db.ex("UPDATE alerts SET acknowledged=1, status='resolved', resolved_at=? WHERE id=?", (db.now(), alert_id))
    return {"acknowledged": alert_id}


@router.post("/alerts/ack-all")
def ack_all():
    db.ex("UPDATE alerts SET acknowledged=1, status='resolved', resolved_at=? WHERE acknowledged=0", (db.now(),))
    return {"acknowledged": "all"}
