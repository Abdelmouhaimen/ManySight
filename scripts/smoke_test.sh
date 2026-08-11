#!/usr/bin/env bash
# Current-contract API smoke test for a running development server.
set -euo pipefail

BASE="${STORELENS_URL:-http://127.0.0.1:8000}/api/v1"
PY=python
command -v python >/dev/null 2>&1 || PY=python3

"$PY" - "$BASE" "${STORELENS_API_KEY:-}" <<'PY'
import sys
import time
import uuid

import requests

base, api_key = sys.argv[1:]
session = requests.Session()
if api_key:
    session.headers["X-API-Key"] = api_key


def request(method, path, *, expected=200, **kwargs):
    response = session.request(method, base + path, timeout=15, **kwargs)
    assert response.status_code == expected, (
        f"{method} {path}: expected {expected}, got {response.status_code}: "
        f"{response.text[:500]}"
    )
    return response.json() if response.content else None


source_id = job_id = analysis_id = None
marker = uuid.uuid4().hex[:10]
now = time.time()
try:
    health = request("GET", "/health")
    assert health["ok"] and health["service"] == "storelens"
    print("ok: health")

    config = request("GET", "/platform-config")
    assert config["rest_url"].endswith("/api/v1")
    contract = request("GET", "/observations/contract")
    assert contract["schema_version"] == 2
    assert set(contract["kinds"]) == {"detection", "measurement", "state"}
    print("ok: discovery and observation contract")

    source = request(
        "POST", "/sources", expected=201,
        json={
            "name": f"Smoke webcam {marker}",
            "kind": "webcam",
            "connection_mode": "agent_local",
            "connection_management": "storelens_managed",
            "connection": {"device_index": 0},
        },
    )
    source_id = source["id"]
    assert "credentials" not in request("GET", f"/sources/{source_id}")
    print("ok: managed source metadata is safe to discover")

    job = request(
        "POST", "/jobs", expected=201,
        json={
            "name": f"Smoke observations {marker}",
            "description": "API contract smoke test",
            "source_ids": [source_id],
            "event_types": ["detection", "measurement", "state"],
        },
    )
    job_id = job["id"]
    worker = request(
        "POST", "/workers", expected=201,
        json={"job_id": job_id, "name": "smoke-worker", "version": "1"},
    )
    heartbeat = request(
        "POST", f"/workers/{worker['id']}/heartbeat",
        json={"status": "running", "metrics": {"samples": 3}},
    )
    assert heartbeat["effective_status"] == "running"
    print("ok: job and worker heartbeat")

    observations = [
        {
            "schema_version": 2,
            "observation_id": f"smoke-detection-{marker}",
            "kind": "detection",
            "timestamp": now,
            "source_id": source_id,
            "worker_id": worker["id"],
            "job_id": job_id,
            "entity_id": "track-1",
            "entity_type": "person",
            "geometry": {"point_px": [320, 470]},
        },
        {
            "schema_version": 2,
            "observation_id": f"smoke-measurement-{marker}",
            "kind": "measurement",
            "timestamp": now,
            "source_id": source_id,
            "worker_id": worker["id"],
            "job_id": job_id,
            "name": "queue_length",
            "value": 1,
            "value_kind": "gauge",
            "unit": "people",
        },
        {
            "schema_version": 2,
            "observation_id": f"smoke-state-{marker}",
            "kind": "state",
            "timestamp": now,
            "source_id": source_id,
            "worker_id": worker["id"],
            "job_id": job_id,
            "name": "door_state",
            "label": "closed",
        },
    ]
    accepted = request("POST", "/observations/batch", json={"observations": observations})
    assert accepted["accepted"] == 3 and not accepted["rejected"]
    latest = request("GET", "/observations/latest", params={"source_id": source_id, "since": 0})
    assert latest["detection"]["entities"] and latest["measurement"]["series"]
    print("ok: schema-v2 ingestion and latest read models")

    query = request(
        "POST", "/analytics/query",
        json={
            "subject": "detection",
            "measures": ["distinct_entities"],
            "filters": {"source_ids": [source_id], "entity_types": ["person"]},
            "range": {"since": now - 1, "until": now + 1},
        },
    )
    assert query["rows"] and query["rows"][0]["distinct_entities"] == 1
    print("ok: derived analytics query")

    analysis = request(
        "POST", "/analyses", expected=201,
        json={
            "name": f"Smoke person count {marker}",
            "question": "How many tracked people were observed?",
            "subject": "detection",
            "measures": ["distinct_entities"],
            "filters": {"source_ids": [source_id], "entity_types": ["person"]},
        },
    )
    analysis_id = analysis["id"]
    assert any(item["id"] == analysis_id for item in request("GET", "/analyses"))
    print("ok: saved analysis")

    forbidden = observations[0] | {
        "observation_id": f"smoke-forbidden-{marker}",
        "zone_id": 1,
    }
    rejected = request("POST", "/observations/batch", json={"observations": [forbidden]})
    assert rejected["accepted"] == 0
    assert rejected["rejected"][0]["error"] == "zone_resolution_forbidden"
    print("ok: worker-owned zone assignment is rejected")
finally:
    if analysis_id is not None:
        request("DELETE", f"/analyses/{analysis_id}")
    if job_id is not None:
        request("DELETE", f"/jobs/{job_id}", params={"purge_events": "true"})
    if source_id is not None:
        request("DELETE", f"/sources/{source_id}")

print("All smoke checks passed.")
PY
