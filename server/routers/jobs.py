"""Analysis jobs — how AI workers (Codex-authored scripts) announce themselves."""
import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import db

router = APIRouter(tags=["jobs"])

STATUSES = {"active", "paused", "done"}


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


def serialize(row: dict) -> dict:
    return {
        "id": row["id"], "name": row["name"], "description": row["description"],
        "source_ids": db.jload(row["source_ids"], []), "event_types": db.jload(row["event_types"], []),
        "status": row["status"], "created_at": row["created_at"],
        "last_event_at": row["last_event_at"], "event_count": row["event_count"],
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
    return serialize(db.q1("SELECT * FROM jobs WHERE id=?", (job_id,)))


@router.delete("/jobs/{job_id}")
def delete_job(job_id: int, purge_events: bool = False):
    if not db.q1("SELECT id FROM jobs WHERE id=?", (job_id,)):
        raise HTTPException(404, "job not found")
    if purge_events:
        db.ex("DELETE FROM events WHERE job_id=?", (job_id,))
    db.ex("DELETE FROM jobs WHERE id=?", (job_id,))
    return {"deleted": job_id, "purged_events": purge_events}
