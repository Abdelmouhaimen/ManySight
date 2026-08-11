"""Analysis jobs and heartbeat-backed external worker instances."""
import json
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import db
from ..services.sse import broker

router = APIRouter(tags=["jobs"])

STATUSES = {"active", "paused", "done"}
WORKER_STATUSES = {"starting", "running", "stopping", "stopped", "error"}
DESIRED_STATES = {"running", "stopped", "restart"}
HEARTBEAT_STALE_S = 30


class JobIn(BaseModel):
    name: str
    description: str = ""
    source_ids: list[int] = []
    event_types: list[str] = []


class JobPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    status: str | None = None
    source_ids: list[int] | None = None
    event_types: list[str] | None = None


class WorkerIn(BaseModel):
    job_id: int
    worker_id: str | None = None
    name: str = ""
    version: str = ""
    config: dict = {}


class WorkerHeartbeat(BaseModel):
    status: str = "running"
    metrics: dict = {}
    last_error: str = ""


class WorkerCommand(BaseModel):
    desired_state: str


def serialize_worker(row: dict) -> dict:
    stale = bool(row["status"] in {"starting", "running", "stopping"} and
                 (not row["last_heartbeat_at"] or db.now() - row["last_heartbeat_at"] > HEARTBEAT_STALE_S))
    return {
        "id": row["id"], "worker_id": row["worker_id"], "job_id": row["job_id"],
        "name": row["name"], "version": row["version"],
        "config": db.jload(row["config_json"], {}), "metrics": db.jload(row["metrics_json"], {}),
        "status": row["status"], "effective_status": "stale" if stale else row["status"],
        "desired_state": row["desired_state"], "started_at": row["started_at"],
        "last_heartbeat_at": row["last_heartbeat_at"], "stopped_at": row["stopped_at"],
        "last_error": row["last_error"], "stale_after_s": HEARTBEAT_STALE_S,
        "created_at": row["created_at"], "updated_at": row["updated_at"],
    }


def serialize(row: dict) -> dict:
    worker = db.q1("SELECT * FROM worker_instances WHERE job_id=? ORDER BY created_at DESC LIMIT 1",
                   (row["id"],))
    return {
        "id": row["id"], "name": row["name"], "description": row["description"],
        "source_ids": db.jload(row["source_ids"], []), "event_types": db.jload(row["event_types"], []),
        "status": row["status"], "created_at": row["created_at"],
        "last_event_at": row["last_event_at"], "event_count": row["event_count"],
        "latest_worker": serialize_worker(worker) if worker else None,
    }


@router.get("/jobs")
def list_jobs():
    return [serialize(r) for r in db.q("SELECT * FROM jobs ORDER BY id DESC")]


@router.post("/jobs", status_code=201)
def create_job(body: JobIn):
    jid = db.ex(
        "INSERT INTO jobs (name, description, source_ids, event_types, created_at) VALUES (?,?,?,?,?)",
        (body.name, body.description, json.dumps(body.source_ids), json.dumps(body.event_types), db.now()),
    )
    return serialize(db.q1("SELECT * FROM jobs WHERE id=?", (jid,)))


@router.get("/jobs/{job_id}")
def get_job(job_id: int):
    row = db.q1("SELECT * FROM jobs WHERE id=?", (job_id,))
    if not row:
        raise HTTPException(404, "job not found")
    return serialize(row)


@router.put("/jobs/{job_id}")
def update_job(job_id: int, body: JobPatch):
    row = db.q1("SELECT * FROM jobs WHERE id=?", (job_id,))
    if not row:
        raise HTTPException(404, "job not found")
    if body.status is not None and body.status not in STATUSES:
        raise HTTPException(422, f"status must be one of {sorted(STATUSES)}")
    sets, args = [], []
    for field, val in (("name", body.name), ("description", body.description), ("status", body.status)):
        if val is not None:
            sets.append(f"{field}=?"); args.append(val)
    if body.source_ids is not None:
        sets.append("source_ids=?"); args.append(json.dumps(body.source_ids))
    if body.event_types is not None:
        sets.append("event_types=?"); args.append(json.dumps(body.event_types))
    if sets:
        db.ex(f"UPDATE jobs SET {', '.join(sets)} WHERE id=?", (*args, job_id))
    if body.status in {"paused", "done"}:
        db.ex("UPDATE worker_instances SET desired_state='stopped', updated_at=? WHERE job_id=?"
              " AND status NOT IN ('stopped','error')", (db.now(), job_id))
    return serialize(db.q1("SELECT * FROM jobs WHERE id=?", (job_id,)))


@router.delete("/jobs/{job_id}")
def delete_job(job_id: int, purge_events: bool = False):
    if not db.q1("SELECT id FROM jobs WHERE id=?", (job_id,)):
        raise HTTPException(404, "job not found")
    if purge_events:
        db.ex("DELETE FROM events WHERE job_id=?", (job_id,))
    db.ex("DELETE FROM worker_instances WHERE job_id=?", (job_id,))
    db.ex("DELETE FROM jobs WHERE id=?", (job_id,))
    return {"deleted": job_id, "purged_events": purge_events}


def _worker(worker_id: int) -> dict:
    row = db.q1("SELECT * FROM worker_instances WHERE id=?", (worker_id,))
    if not row:
        raise HTTPException(404, "worker instance not found")
    return row


@router.get("/workers")
def list_workers(job_id: int | None = None):
    where, args = ("WHERE job_id=?", (job_id,)) if job_id is not None else ("", ())
    return [serialize_worker(r) for r in db.q(
        f"SELECT * FROM worker_instances {where} ORDER BY created_at DESC", args)]


@router.post("/workers", status_code=201)
def register_worker(body: WorkerIn):
    if not db.q1("SELECT id FROM jobs WHERE id=?", (body.job_id,)):
        raise HTTPException(404, "job not found — register a job first")
    external_id = body.worker_id or str(uuid.uuid4())
    now = db.now()
    existing = db.q1("SELECT * FROM worker_instances WHERE worker_id=?", (external_id,))
    if existing:
        if existing["job_id"] != body.job_id:
            raise HTTPException(409, "worker_id is already registered to another job")
        db.ex("UPDATE worker_instances SET name=?,version=?,config_json=?,status='starting',"
              " desired_state='running',started_at=?,last_heartbeat_at=?,stopped_at=NULL,"
              " last_error='',updated_at=? WHERE id=?",
              (body.name, body.version, json.dumps(body.config), now, now, now, existing["id"]))
        return serialize_worker(_worker(existing["id"]))
    wid = db.ex(
        "INSERT INTO worker_instances (worker_id,job_id,name,version,config_json,status,desired_state,"
        " started_at,last_heartbeat_at,created_at,updated_at) VALUES (?,?,?,?,?,'starting','running',?,?,?,?)",
        (external_id, body.job_id, body.name, body.version, json.dumps(body.config), now, now, now, now))
    return serialize_worker(_worker(wid))


@router.get("/workers/{worker_id}")
def get_worker(worker_id: int):
    return serialize_worker(_worker(worker_id))


@router.post("/workers/{worker_id}/heartbeat")
def heartbeat_worker(worker_id: int, body: WorkerHeartbeat):
    row = _worker(worker_id)
    if body.status not in WORKER_STATUSES:
        raise HTTPException(422, f"status must be one of {sorted(WORKER_STATUSES)}")
    now = db.now()
    stopped = now if body.status in {"stopped", "error"} else row["stopped_at"]
    db.ex("UPDATE worker_instances SET status=?,metrics_json=?,last_error=?,last_heartbeat_at=?,"
          " stopped_at=?,updated_at=? WHERE id=?",
          (body.status, json.dumps(body.metrics), body.last_error, now, stopped, now, worker_id))
    current = serialize_worker(_worker(worker_id))
    broker.publish("worker.updated", current)
    return {**current, "should_stop": current["desired_state"] in {"stopped", "restart"},
            "restart_requested": current["desired_state"] == "restart"}


@router.put("/workers/{worker_id}/desired-state")
def command_worker(worker_id: int, body: WorkerCommand):
    _worker(worker_id)
    if body.desired_state not in DESIRED_STATES:
        raise HTTPException(422, f"desired_state must be one of {sorted(DESIRED_STATES)}")
    db.ex("UPDATE worker_instances SET desired_state=?,updated_at=? WHERE id=?",
          (body.desired_state, db.now(), worker_id))
    current = serialize_worker(_worker(worker_id))
    broker.publish("worker.updated", current)
    return current
