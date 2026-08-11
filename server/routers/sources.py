"""Logical observation sources, managed connection metadata, and geometry.

StoreLens never opens or proxies a source. Workers may explicitly resolve a managed
connection through a separately authenticated endpoint; ordinary source reads remain
safe to display and never contain credentials.
"""
import hmac
import json
import os
import re
from urllib.parse import parse_qsl, urlsplit

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .. import db
from ..services import homography
from ..services import credentials as credential_store
from .jobs import serialize_worker

router = APIRouter(tags=["sources"])

KINDS = {"rtsp", "webrtc", "http", "webcam", "file", "sensor", "custom"}
CONNECTION_MODES = {"agent_local", "edge_gateway"}
CONNECTION_MANAGEMENT = {"external_secret", "storelens_managed"}
VIDEO_KINDS = {"rtsp", "webrtc", "http", "webcam", "file"}
FORBIDDEN_LOCATOR_KEYS = {
    "url", "uri", "username", "password", "token", "api_key", "apikey",
    "access_token", "auth_token", "signature", "sig", "secret", "credential",
    "credentials", "connection_string",
}


class SourceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    kind: str = "webcam"
    connection_mode: str = "agent_local"
    connection_management: str = "external_secret"
    connection: dict = Field(default_factory=dict)
    credentials: dict | None = None
    locator: dict = Field(default_factory=dict)
    capabilities: list[str] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)


class SourcePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    kind: str | None = None
    connection_mode: str | None = None
    connection_management: str | None = None
    connection: dict | None = None
    credentials: dict | None = None
    clear_credentials: bool = False
    locator: dict | None = None
    capabilities: list[str] | None = None
    metadata: dict | None = None


class Placement(BaseModel):
    x: float
    y: float
    rotation_deg: float = 0
    fov_deg: float = 70


class CalibrationIn(BaseModel):
    points: list[dict]  # [{"px": {x,y}, "map": {x,y}}, ...] >= 4
    frame_w: int | None = None
    frame_h: int | None = None


class ProjectIn(BaseModel):
    points: list[dict]  # [{x,y}] pixel coords
    surface_id: int | None = None  # null = saved floor calibration


class UnprojectIn(BaseModel):
    points: list[dict]  # [{x,y}] map metres
    surface_id: int | None = None


def _validate_source(kind: str, connection_mode: str, management: str,
                     connection: dict, locator: dict, *, allow_unconfigured: bool = False):
    if kind not in KINDS:
        raise HTTPException(422, f"kind must be one of {sorted(KINDS)}")
    if connection_mode not in CONNECTION_MODES:
        raise HTTPException(422, f"connection_mode must be one of {sorted(CONNECTION_MODES)}")
    if management not in CONNECTION_MANAGEMENT:
        raise HTTPException(422, f"connection_management must be one of {sorted(CONNECTION_MANAGEMENT)}")

    def walk(value, path="locator"):
        if isinstance(value, dict):
            for key, item in value.items():
                normalized = str(key).lower().replace("-", "_")
                if normalized in FORBIDDEN_LOCATOR_KEYS:
                    raise HTTPException(
                        422,
                        f"{path}.{key} may contain camera access or credentials; use a "
                        "storelens_managed connection or an external_secret local_secret_ref instead",
                    )
                walk(item, f"{path}.{key}")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, f"{path}[{index}]")
        elif isinstance(value, str) and re.match(r"^(rtsp|rtsps|https?)://", value, re.I):
            raise HTTPException(
                422,
                f"{path} must not contain a network camera URL; use a storelens_managed "
                "connection or an external_secret local_secret_ref instead",
            )

    walk(locator)
    if management == "external_secret":
        if connection:
            raise HTTPException(422, "external_secret sources use locator.local_secret_ref, not connection")
        ref = locator.get("local_secret_ref")
        if ref is not None and (not isinstance(ref, str) or not ref.strip()):
            raise HTTPException(422, "locator.local_secret_ref must be a non-empty string")
        return

    if locator:
        raise HTTPException(422, "storelens_managed sources use connection, not locator")
    if kind not in {"webcam", "rtsp", "http", "file"}:
        raise HTTPException(422, f"managed connections are not supported for source kind {kind}")
    allowed = {
        "webcam": {"device_index"},
        "rtsp": {"host", "port", "path", "transport", "scheme"},
        "http": {"url", "auth_type"},
        "file": {"path"},
    }[kind]
    unknown = set(connection) - allowed
    if unknown:
        raise HTTPException(422, f"unsupported {kind} connection fields: {sorted(unknown)}")
    if allow_unconfigured and not connection:
        return
    if kind == "webcam":
        index = connection.get("device_index")
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            raise HTTPException(422, "webcam connection.device_index must be a non-negative integer")
    elif kind == "rtsp":
        host = connection.get("host")
        if not isinstance(host, str) or not host.strip() or "://" in host or "@" in host or "/" in host:
            raise HTTPException(422, "rtsp connection.host must be a host name or IP address")
        port = connection.get("port", 554)
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise HTTPException(422, "rtsp connection.port must be between 1 and 65535")
        path = connection.get("path", "/")
        if not isinstance(path, str) or not path.startswith("/"):
            raise HTTPException(422, "rtsp connection.path must start with /")
        query_keys = {key.lower().replace("-", "_") for key, _ in parse_qsl(urlsplit(path).query)}
        if query_keys & FORBIDDEN_LOCATOR_KEYS:
            raise HTTPException(422, "rtsp connection.path must not contain credential query parameters")
        if connection.get("transport", "tcp") not in {"tcp", "udp"}:
            raise HTTPException(422, "rtsp connection.transport must be tcp or udp")
        if connection.get("scheme", "rtsp") not in {"rtsp", "rtsps"}:
            raise HTTPException(422, "rtsp connection.scheme must be rtsp or rtsps")
    elif kind == "http":
        url = connection.get("url")
        parsed = urlsplit(url) if isinstance(url, str) else None
        if not parsed or parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise HTTPException(422, "http connection.url must be an absolute HTTP(S) URL")
        if parsed.username is not None or parsed.password is not None:
            raise HTTPException(422, "http connection.url must not contain credentials")
        query_keys = {key.lower().replace("-", "_") for key, _ in parse_qsl(parsed.query)}
        if query_keys & FORBIDDEN_LOCATOR_KEYS:
            raise HTTPException(422, "http connection.url must not contain credential query parameters")
        if connection.get("auth_type", "none") not in {"none", "basic"}:
            raise HTTPException(422, "http connection.auth_type must be none or basic")
    elif kind == "file":
        path = connection.get("path")
        if not isinstance(path, str) or not path.strip():
            raise HTTPException(422, "file connection.path is required")


def _validate_credentials(kind: str, management: str, values: dict | None):
    if values is None:
        return
    if management != "storelens_managed":
        raise HTTPException(422, "credentials are only valid for storelens_managed sources")
    if not values:
        raise HTTPException(422, "credentials must not be empty; use clear_credentials to remove them")
    allowed = {"rtsp": {"username", "password"}, "http": {"username", "password"}}.get(kind, set())
    unknown = set(values) - allowed
    if unknown:
        raise HTTPException(422, f"unsupported {kind} credential fields: {sorted(unknown)}")
    if any(not isinstance(value, str) for value in values.values()):
        raise HTTPException(422, "credential values must be strings")


def _encrypted(values: dict) -> str:
    try:
        return credential_store.encrypt(values)
    except credential_store.CredentialConfigurationError as exc:
        raise HTTPException(503, str(exc)) from exc


def _credential_status(source_id: int) -> dict:
    row = db.q1("SELECT credential_type FROM source_credentials WHERE source_id=?", (source_id,))
    fields = set((row or {}).get("credential_type", "").split(","))
    return {"configured": bool(row), "username_configured": "username" in fields}


def _runtime_by_source() -> dict[int, dict]:
    runtime: dict[int, dict] = {}
    for job in db.q("SELECT id, name, source_ids, status FROM jobs ORDER BY created_at DESC"):
        worker = db.q1(
            "SELECT * FROM worker_instances WHERE job_id=? ORDER BY created_at DESC LIMIT 1",
            (job["id"],),
        )
        summary = {
            "job_id": job["id"],
            "job_name": job["name"],
            "job_status": job["status"],
            "worker": serialize_worker(worker) if worker else None,
        }
        for source_id in db.jload(job["source_ids"], []):
            try:
                runtime.setdefault(int(source_id), summary)
            except (TypeError, ValueError):
                continue
    return runtime


def serialize(row: dict, runtime: dict | None = None) -> dict:
    cal = db.jload(row.get("calibration_json"), None) if row.get("calibration_json") else None
    last_ingestion = row.get("last_ingestion_at")
    age = max(0.0, db.now() - last_ingestion) if last_ingestion else None
    observation_status = (
        "never" if age is None else
        "active" if age <= 30 else
        "recent" if age <= 300 else
        "stale"
    )
    return {
        "id": row["id"],
        "name": row["name"],
        "kind": row["kind"],
        "connection_mode": row.get("connection_mode") or "agent_local",
        "connection_management": row.get("connection_management") or "external_secret",
        "connection": db.jload(row.get("connection_config_json"), {}),
        "connection_revision": int(row.get("connection_revision") or 0),
        "credential_status": _credential_status(row["id"]),
        "locator": db.jload(row.get("locator_json"), {}),
        "capabilities": db.jload(row.get("capabilities_json"), []),
        "metadata": db.jload(row.get("metadata_json"), {}),
        "observation_status": observation_status,
        "last_observation_at": row.get("last_observation_at"),
        "last_ingestion_at": last_ingestion,
        "observation_age_s": age,
        "event_count": int(row.get("event_count") or 0),
        "placement": (
            {"x": row["map_x"], "y": row["map_y"], "rotation_deg": row["rotation_deg"], "fov_deg": row["fov_deg"]}
            if row["map_x"] is not None else None
        ),
        "calibrated": bool(cal and cal.get("H")),
        "calibration": cal,
        "calibration_revision": row.get("calibration_revision", 0),
        "latest_runtime": runtime,
        "created_at": row["created_at"],
    }


def _get(source_id: int) -> dict:
    row = db.q1("SELECT * FROM sources WHERE id=?", (source_id,))
    if not row:
        raise HTTPException(404, "source not found")
    return row


@router.get("/sources", summary="List safe logical source metadata")
def list_sources():
    runtime = _runtime_by_source()
    return [serialize(r, runtime.get(r["id"])) for r in db.q("SELECT * FROM sources ORDER BY id")]


@router.post("/sources", status_code=201, summary="Create a managed or external-secret source")
def create_source(body: SourceIn):
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "name is required")
    _validate_source(body.kind, body.connection_mode, body.connection_management,
                     body.connection, body.locator, allow_unconfigured=False)
    _validate_credentials(body.kind, body.connection_management, body.credentials)
    encrypted = _encrypted(body.credentials) if body.credentials else None
    capabilities = body.capabilities or (["video"] if body.kind in VIDEO_KINDS else [])
    con = db.connect()
    try:
        now = db.now()
        cursor = con.execute(
            "INSERT INTO sources (name,kind,connection_mode,connection_management,connection_config_json,"
            "connection_revision,locator_json,capabilities_json,metadata_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (name, body.kind, body.connection_mode, body.connection_management, json.dumps(body.connection),
             1 if body.connection else 0, json.dumps(body.locator), json.dumps(capabilities), json.dumps(body.metadata), now),
        )
        sid = cursor.lastrowid
        if encrypted:
            con.execute("INSERT INTO source_credentials VALUES (?,?,?,?,?)",
                        (sid, encrypted, ",".join(sorted(body.credentials)), now, now))
        con.commit()
    finally:
        con.close()
    return serialize(_get(sid))


@router.get("/sources/{source_id}", summary="Get safe logical source metadata")
def get_source(source_id: int):
    return serialize(_get(source_id), _runtime_by_source().get(source_id))


@router.put("/sources/{source_id}", summary="Update source metadata or protected connection configuration")
def update_source(source_id: int, body: SourcePatch):
    row = _get(source_id)
    kind = body.kind or row["kind"]
    mode = body.connection_mode or row.get("connection_mode") or "agent_local"
    management = body.connection_management or row.get("connection_management") or "external_secret"
    connection = body.connection if body.connection is not None else db.jload(row.get("connection_config_json"), {})
    locator = body.locator if body.locator is not None else db.jload(row.get("locator_json"), {})
    if body.connection_management == "storelens_managed" and body.locator is None:
        locator = {}
    if body.connection_management == "external_secret" and body.connection is None:
        connection = {}
    _validate_source(kind, mode, management, connection, locator, allow_unconfigured=False)
    _validate_credentials(kind, management, body.credentials)
    if body.credentials is not None and body.clear_credentials:
        raise HTTPException(422, "credentials and clear_credentials are mutually exclusive")
    encrypted = _encrypted(body.credentials) if body.credentials else None
    fields = {
        "name": body.name.strip() if body.name is not None else None,
        "kind": body.kind,
        "connection_mode": body.connection_mode,
        "connection_management": body.connection_management,
        "connection_config_json": json.dumps(connection) if (body.connection is not None or body.connection_management is not None) else None,
        "locator_json": json.dumps(locator) if (body.locator is not None or body.connection_management is not None) else None,
        "capabilities_json": json.dumps(body.capabilities) if body.capabilities is not None else None,
        "metadata_json": json.dumps(body.metadata) if body.metadata is not None else None,
    }
    sets = {k: v for k, v in fields.items() if v is not None}
    if body.name is not None and not fields["name"]:
        raise HTTPException(422, "name is required")
    connection_changed = (
        body.connection is not None or body.connection_management is not None or body.kind is not None
        or body.credentials is not None or body.clear_credentials
    )
    if connection_changed:
        sets["connection_revision"] = int(row.get("connection_revision") or 0) + 1
    con = db.connect()
    try:
        if sets:
            con.execute(f"UPDATE sources SET {', '.join(f'{k}=?' for k in sets)} WHERE id=?", (*sets.values(), source_id))
        if body.clear_credentials or management != "storelens_managed":
            con.execute("DELETE FROM source_credentials WHERE source_id=?", (source_id,))
        elif encrypted:
            now = db.now()
            con.execute(
                "INSERT INTO source_credentials VALUES (?,?,?,?,?) ON CONFLICT(source_id) DO UPDATE SET "
                "encrypted_payload=excluded.encrypted_payload, credential_type=excluded.credential_type, updated_at=excluded.updated_at",
                (source_id, encrypted, ",".join(sorted(body.credentials)), now, now),
            )
        con.commit()
    finally:
        con.close()
    return serialize(_get(source_id))


def _require_credential_access(request: Request):
    expected = os.environ.get("STORELENS_CREDENTIAL_ACCESS_KEY") or os.environ.get("STORELENS_API_KEY")
    if not expected:
        raise HTTPException(503, "credential resolution is disabled; configure STORELENS_CREDENTIAL_ACCESS_KEY")
    supplied = request.headers.get("x-storelens-credential-key") or request.headers.get("x-api-key")
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(401, "invalid or missing credential access key")


@router.get(
    "/sources/{source_id}/connection",
    summary="Resolve a source connection for an authorized worker",
    description="Sensitive, header-authenticated endpoint. Do not log or persist its response.",
)
def get_source_connection(source_id: int, request: Request):
    _require_credential_access(request)
    row = _get(source_id)
    management = row.get("connection_management") or "external_secret"
    result = {
        "source_id": source_id,
        "kind": row["kind"],
        "connection_management": management,
        "connection": db.jload(row.get("connection_config_json"), {}),
        "connection_revision": int(row.get("connection_revision") or 0),
    }
    if management == "storelens_managed":
        stored = db.q1("SELECT encrypted_payload FROM source_credentials WHERE source_id=?", (source_id,))
        if stored:
            try:
                result["connection"].update(credential_store.decrypt(stored["encrypted_payload"]))
            except credential_store.CredentialConfigurationError as exc:
                raise HTTPException(503, str(exc)) from exc
            except credential_store.CredentialDecryptionError as exc:
                raise HTTPException(500, str(exc)) from exc
    else:
        result["connection"] = db.jload(row.get("locator_json"), {})
    return result


@router.delete("/sources/{source_id}", summary="Delete a source and its geometry configuration")
def delete_source(source_id: int):
    _get(source_id)
    db.ex("DELETE FROM zone_views WHERE source_id=?", (source_id,))
    db.ex("DELETE FROM projection_surfaces WHERE source_id=?", (source_id,))
    db.ex("DELETE FROM source_credentials WHERE source_id=?", (source_id,))
    db.ex("DELETE FROM sources WHERE id=?", (source_id,))
    return {"deleted": source_id}


@router.put("/sources/{source_id}/placement")
def set_placement(source_id: int, body: Placement):
    _get(source_id)
    db.ex(
        "UPDATE sources SET map_x=?, map_y=?, rotation_deg=?, fov_deg=? WHERE id=?",
        (body.x, body.y, body.rotation_deg, body.fov_deg, source_id),
    )
    return serialize(_get(source_id))


@router.delete("/sources/{source_id}/placement")
def clear_placement(source_id: int):
    _get(source_id)
    db.ex("UPDATE sources SET map_x=NULL, map_y=NULL WHERE id=?", (source_id,))
    return serialize(_get(source_id))


@router.put("/sources/{source_id}/calibration")
def set_calibration(source_id: int, body: CalibrationIn):
    row = _get(source_id)
    try:
        H, err = homography.compute_homography(body.points)
    except ValueError as e:
        raise HTTPException(422, str(e))
    import json
    revision = int(row.get("calibration_revision") or 0) + 1
    cal = {"points": body.points, "H": H, "error_m": err, "frame_w": body.frame_w,
           "frame_h": body.frame_h, "revision": revision, "plane": "floor"}
    db.ex("UPDATE sources SET calibration_json=?, calibration_revision=? WHERE id=?",
          (json.dumps(cal), revision, source_id))
    return {"H": H, "error_m": err, "points": len(body.points), "revision": revision,
            "plane": "floor"}


@router.delete("/sources/{source_id}/calibration")
def clear_calibration(source_id: int):
    row = _get(source_id)
    revision = int(row.get("calibration_revision") or 0) + 1
    db.ex("UPDATE sources SET calibration_json=NULL, calibration_revision=? WHERE id=?",
          (revision, source_id))
    return {"cleared": True, "revision": revision}


def _projection(source_id: int, surface_id: int | None) -> tuple[list, str, int]:
    row = _get(source_id)
    if surface_id is not None:
        surface = db.q1("SELECT * FROM projection_surfaces WHERE id=?", (surface_id,))
        if not surface:
            raise HTTPException(404, "projection surface not found")
        if surface["source_id"] != source_id:
            raise HTTPException(422, "projection surface belongs to a different source")
        return db.jload(surface["homography_json"], None), surface["name"], surface["revision"]
    cal = db.jload(row.get("calibration_json"), None)
    if not cal or not cal.get("H"):
        raise HTTPException(409, "source floor is not calibrated — set at least 4 point pairs first")
    return cal["H"], "floor", int(row.get("calibration_revision") or 0)


@router.post("/sources/{source_id}/project")
def project_points(source_id: int, body: ProjectIn):
    H, surface, revision = _projection(source_id, body.surface_id)
    pts = homography.project(H, body.points)
    return {"points": [{"x": p[0], "y": p[1]} for p in pts],
            "surface": surface, "surface_id": body.surface_id, "revision": revision}


@router.post("/sources/{source_id}/unproject")
def unproject_points(source_id: int, body: UnprojectIn):
    """Map metres -> camera pixels on the selected plane. A floor transform must
    not be used to compensate for the height of an elevated surface."""
    H, surface, revision = _projection(source_id, body.surface_id)
    try:
        inverse = homography.invert(H)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    pts = homography.project(inverse, body.points)
    return {"points": [{"x": p[0], "y": p[1]} for p in pts],
            "surface": surface, "surface_id": body.surface_id, "revision": revision}
