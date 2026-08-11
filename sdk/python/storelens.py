"""StoreLens worker SDK — a tiny client for analysis scripts (the ones Codex writes).

Observe locally, derive centrally: workers submit only three observation kinds —
detection (an observed entity with spatial evidence), measurement (an observed
numeric value), and state (an observed current categorical value). The platform
derives zones, visits, dwell, occupancy, movement, state transitions/durations,
and every analysis from these raw rows. Workers must never resolve zones, send
zone_id/zone, or calculate zone entry/exit, dwell, occupancy, state changes, or
durations — see get_observation_contract()/GET /api/v1/observations/contract.

Typical worker loop:
    from storelens import StoreLens, CentroidTracker
    sl = StoreLens("http://localhost:8000")
    src = sl.source(1)  # logical metadata only; no camera credential is returned
    job = sl.register_job("Checkout presence", event_types=["detection"])
    worker = sl.register_worker("checkout-worker", version="1")
    cap = sl.open_capture(src, local_connection=0)
    ...
    command = sl.heartbeat(metrics={"fps": fps})
    if command["should_stop"]:
        break
    sl.submit_detection(source_id=src["id"], entity_id=tid, point_px=(u, v))
    sl.flush()   # or use `with sl.batch():` — observations auto-flush every `batch_size`
"""
import atexit
import json
import os
import time
import uuid
from urllib.parse import quote, urlsplit, urlunsplit

import requests


class StoreLens:
    def __init__(self, base_url: str = "http://localhost:8000", api_key: str = "",
                 batch_size: int = 200, credential_access_key: str | None = None):
        self.base = base_url.rstrip("/") + "/api/v1"
        self.session = requests.Session()
        if api_key:
            self.session.headers["X-API-Key"] = api_key
        self.credential_access_key = (
            credential_access_key if credential_access_key is not None
            else os.environ.get("STORELENS_CREDENTIAL_ACCESS_KEY", api_key)
        )
        self.batch_size = batch_size
        self.job_id = None
        self.worker_instance_id = None
        self._buffer: list[dict] = []       # legacy /events buffer
        self._obs_buffer: list[dict] = []   # current /observations/batch buffer
        atexit.register(self.flush)

    # ---------- HTTP ----------
    def _req(self, method: str, path: str, body=None, params=None):
        r = self.session.request(method, self.base + path, json=body, params=params, timeout=30)
        if not r.ok:
            raise RuntimeError(f"{method} {path} -> {r.status_code}: {r.text[:300]}")
        return r.json()

    # ---------- discovery ----------
    def sources(self) -> list[dict]:
        return self._req("GET", "/sources")

    def source(self, source_id: int) -> dict:
        return self._req("GET", f"/sources/{source_id}")

    def get_source_connection(self, source_id: int) -> dict:
        """Resolve sensitive connection material for immediate worker use.

        The returned dictionary must not be logged or persisted. This call uses the
        dedicated credential access key rather than relying on public source reads.
        """
        if not self.credential_access_key:
            raise RuntimeError(
                "STORELENS_CREDENTIAL_ACCESS_KEY is required to resolve a managed source"
            )
        response = self.session.get(
            self.base + f"/sources/{source_id}/connection",
            headers={"X-StoreLens-Credential-Key": self.credential_access_key},
            timeout=30,
        )
        if not response.ok:
            raise RuntimeError(
                f"source {source_id} credential resolution failed with HTTP {response.status_code}"
            )
        return response.json()

    def create_source(self, name: str, kind: str = "webcam",
                      connection_mode: str = "agent_local", locator=None,
                      capabilities=None, metadata=None,
                      connection_management: str = "external_secret",
                      connection=None, credentials=None) -> dict:
        """Register either a StoreLens-managed or external-secret source."""
        return self._req("POST", "/sources", {
            "name": name,
            "kind": kind,
            "connection_mode": connection_mode,
            "locator": locator or {},
            "connection_management": connection_management,
            "connection": connection or {},
            "credentials": credentials,
            "capabilities": capabilities or [],
            "metadata": metadata or {},
        })

    def update_source(self, source_id: int, **patch) -> dict:
        return self._req("PUT", f"/sources/{source_id}", patch)

    def store_map(self) -> dict:
        m = self._req("GET", "/store")
        m["zones"] = self.zones()
        m["projection_surfaces"] = self.projection_surfaces()
        m["zone_views"] = self.zone_views()
        return m

    def zones(self) -> list[dict]:
        return self._req("GET", "/zones")

    def projection_surfaces(self, source_id: int | None = None) -> list[dict]:
        return self._req("GET", "/projection-surfaces", params={"source_id": source_id} if source_id else None)

    def zone_views(self, source_id: int | None = None, zone_id: int | None = None) -> list[dict]:
        params = {k: v for k, v in {"source_id": source_id, "zone_id": zone_id}.items() if v is not None}
        return self._req("GET", "/zone-views", params=params)

    def create_projection_surface(self, **definition) -> dict:
        return self._req("POST", "/projection-surfaces", definition)

    def create_zone_view(self, **definition) -> dict:
        return self._req("POST", "/zone-views", definition)

    def zone_by_name(self, name: str) -> dict | None:
        return next((z for z in self.zones() if z["name"].lower() == name.lower()), None)

    # ---------- jobs & events ----------
    def register_job(self, name: str, description: str = "", source_ids=None, event_types=None) -> dict:
        job = self._req("POST", "/jobs", {"name": name, "description": description,
                                          "source_ids": source_ids or [], "event_types": event_types or []})
        self.job_id = job["id"]
        return job

    def register_worker(self, name: str = "", version: str = "", config=None,
                        worker_id: str | None = None) -> dict:
        """Register a concrete worker process after register_job. Heartbeat regularly;
        the dashboard can request stop/restart but does not spawn arbitrary processes."""
        if self.job_id is None:
            raise RuntimeError("register_job before register_worker")
        body = {"job_id": self.job_id, "name": name, "version": version,
                "config": config or {}}
        if worker_id:
            body["worker_id"] = worker_id
        worker = self._req("POST", "/workers", body)
        self.worker_instance_id = worker["id"]
        return worker

    def heartbeat(self, status: str = "running", metrics=None, last_error: str = "") -> dict:
        """Report liveness and receive cooperative stop/restart commands.
        Call every 5â€“15 seconds and exit when `should_stop` is true."""
        if self.worker_instance_id is None:
            raise RuntimeError("register_worker before heartbeat")
        return self._req("POST", f"/workers/{self.worker_instance_id}/heartbeat",
                         {"status": status, "metrics": metrics or {}, "last_error": last_error})

    def stop_worker(self, error: str = "") -> dict | None:
        if self.worker_instance_id is None:
            return None
        self.flush()
        return self.heartbeat("error" if error else "stopped", last_error=error)

    # ---------- observations (current contract: detection | measurement | state) ----------
    def add_observation(self, kind: str, source_id: int, **fields) -> None:
        """Buffer one observation; flushes automatically at batch_size. Prefer the
        typed submit_detection/submit_measurement/submit_state helpers below —
        this is their shared plumbing. Never pass zone_id/zone or a legacy kind
        (zone_enter/zone_exit/zone_dwell/state_change/count); StoreLens rejects
        those with a legacy_derived_observation error."""
        if kind not in {"detection", "measurement", "state"}:
            raise ValueError("kind must be detection, measurement, or state — "
                             "StoreLens derives zone/dwell/occupancy/state-change events itself")
        observation = {
            "schema_version": 2,
            "observation_id": fields.pop("observation_id", None) or str(uuid.uuid4()),
            "kind": kind, "timestamp": fields.pop("ts", None) or time.time(), "source_id": source_id,
            "worker_id": fields.pop("worker_id", None) or self.worker_instance_id,
            "job_id": fields.pop("job_id", None) or self.job_id,
        }
        observation.update({k: v for k, v in fields.items() if v is not None})
        self._obs_buffer.append(observation)
        if len(self._obs_buffer) >= self.batch_size:
            self.flush_observations()

    def submit_detection(self, source_id: int, entity_id: str | None = None,
                         point_px: tuple | None = None, bbox_px: tuple | None = None,
                         keypoints_px: dict | None = None, point_map: tuple | dict | None = None,
                         label: str | None = None, entity_type: str | None = None,
                         confidence: float | None = None, attributes: dict | None = None,
                         identity_scope: str = "worker_run", identity_model_version: str | None = None,
                         ts: float | None = None, observation_id: str | None = None) -> None:
        """Buffer one observed entity with spatial evidence. `point_px` is [x,y];
        `bbox_px` is [x0,y0,x1,y1] (corner form, not [x,y,w,h]); `keypoints_px` is
        {name: [x,y]} (e.g. {"left_ankle": [190,455]}) — StoreLens picks the
        representative point in that precedence: point_px, then foot/ankle
        keypoints, then bbox bottom-center. `point_map` ([x,y] or {x,y} in map
        metres) is only for a trusted non-camera producer that already knows the
        floor position (e.g. a simulator or a non-visual sensor); a camera worker
        should send pixel evidence instead and let StoreLens project it.
        `entity_id` is an opaque per-track id (never a verified human identity);
        `identity_scope` documents how far it is safe to treat two entity_ids as
        "the same" (default: only within this worker run)."""
        geometry = {}
        if point_px is not None:
            geometry["point_px"] = list(point_px)
        if bbox_px is not None:
            geometry["bbox_px"] = list(bbox_px)
        if keypoints_px is not None:
            geometry["keypoints_px"] = {k: list(v) for k, v in keypoints_px.items()}
        if point_map is not None:
            geometry["point_map"] = ({"x": point_map[0], "y": point_map[1]}
                                     if not isinstance(point_map, dict) else point_map)
        self.add_observation(
            "detection", source_id, entity_id=entity_id, entity_type=entity_type, label=label,
            confidence=confidence, attributes=attributes, geometry=geometry or None,
            identity_scope=identity_scope, identity_model_version=identity_model_version,
            ts=ts, observation_id=observation_id)

    def submit_measurement(self, source_id: int, name: str, value: float, label: str | None = None,
                           value_kind: str = "gauge", unit: str | None = None,
                           entity_id: str | None = None, point_map: tuple | dict | None = None,
                           confidence: float | None = None, attributes: dict | None = None,
                           ts: float | None = None, observation_id: str | None = None) -> None:
        """Buffer one observed numeric sample. `value_kind`: gauge (instantaneous,
        default — e.g. people currently waiting), delta (an increment observed
        this sample), or cumulative (a monotonically increasing producer
        counter — StoreLens detects resets so a worker restart never produces a
        negative rate). Never post a time-aggregated or precomputed total.
        A measurement can only be zone-assigned if it carries geometry (e.g.
        `point_map`, for a count with no single associated entity) or shares an
        `entity_id` with a recent detection — omit both and it simply won't be
        zoned."""
        geometry = None
        if point_map is not None:
            geometry = {"point_map": ({"x": point_map[0], "y": point_map[1]}
                                      if not isinstance(point_map, dict) else point_map)}
        self.add_observation("measurement", source_id, name=name, value=value, value_kind=value_kind,
                             unit=unit, label=label, entity_id=entity_id, confidence=confidence,
                             attributes=attributes, geometry=geometry, ts=ts, observation_id=observation_id)

    def submit_detection_frame(self, source_id: int, entity_type: str, count: int,
                               ts: float | None = None, attributes: dict | None = None,
                               observation_id: str | None = None) -> None:
        """Submit the entity count observed in one processed frame.

        Submit this once per inference sample with the same timestamp used by
        every detection from that frame. The value, including zero, is the
        instantaneous camera/entity-type count at that exact timestamp.
        """
        if count < 0:
            raise ValueError("detection frame count must be non-negative")
        self.submit_measurement(
            source_id=source_id,
            name="detection_frame_count",
            value=int(count),
            label=entity_type,
            value_kind="gauge",
            unit="tracks",
            attributes=attributes,
            ts=ts,
            observation_id=observation_id,
        )

    def submit_state(self, source_id: int, name: str, label: str, entity_id: str | None = None,
                     info: dict | None = None, confidence: float | None = None,
                     ts: float | None = None, observation_id: str | None = None) -> None:
        """Buffer one observed current categorical state (e.g. name="door_state",
        label="open"). Send this on every sample, not only on change — StoreLens
        coalesces repeated identical samples into intervals and derives
        transitions/durations itself; never send a computed duration or a
        state_change event. Set `entity_id` when more than one independently
        stateful entity shares this source and name (e.g. two fridges)."""
        self.add_observation("state", source_id, name=name, label=label, entity_id=entity_id,
                             info=info, confidence=confidence, ts=ts, observation_id=observation_id)

    def flush_observations(self) -> dict | None:
        if not self._obs_buffer:
            return None
        batch, self._obs_buffer = self._obs_buffer, []
        return self._req("POST", "/observations/batch", {"job_id": self.job_id, "observations": batch})

    def submit_observations(self, observations: list[dict]) -> dict:
        """Post a batch of already-built observation dicts immediately (bypasses
        the buffer). Prefer submit_detection/submit_measurement/submit_state for
        normal use."""
        return self._req("POST", "/observations/batch", {"job_id": self.job_id, "observations": observations})

    def query_analytics(self, subject: str, measures: list[str], filters: dict | None = None,
                        grouping: dict | None = None, range: dict | None = None,
                        comparison: dict | None = None) -> dict:
        """Answer one analytical question directly — see
        server/routers/analytics_query.py for the full subject/measure/grouping
        vocabulary, or GET /api/v1/analytics/capabilities for what fits the data
        actually present."""
        return self._req("POST", "/analytics/query", {
            "subject": subject, "measures": measures, "filters": filters or {},
            "grouping": grouping or {}, "range": range or {}, "comparison": comparison or {}})

    def save_analysis(self, name: str, subject: str, measures: list[str], filters: dict | None = None,
                      grouping: dict | None = None, **kwargs) -> dict:
        """Save a data question so it appears on the dashboard. This is a
        question, not a chart — switching how it renders later is a `patch` on
        the same record (`presentation`), never a second analysis."""
        return self._req("POST", "/analyses", {
            "name": name, "subject": subject, "measures": measures,
            "filters": filters or {}, "grouping": grouping or {}, **kwargs})

    # ---------- legacy events (event_type-based contract) ----------
    _LEGACY_DERIVED_TYPES = {"zone_enter", "zone_exit", "zone_dwell", "state_change", "count"}

    def add_event(self, **event):
        """DEPRECATED for new work — prefer submit_detection/submit_measurement/
        submit_state. Buffers one legacy event; flushes automatically at
        batch_size. Still works against /events for backward compatibility, but
        zone_enter/zone_exit/zone_dwell/state_change/count are events StoreLens
        now derives itself — a new worker should never compute and send them."""
        if event.get("event_type") in self._LEGACY_DERIVED_TYPES:
            import warnings
            warnings.warn(
                f"add_event(event_type='{event['event_type']}') sends a platform-derived event. "
                "New workers should submit_detection/submit_measurement/submit_state instead and "
                "let StoreLens derive dwell/occupancy/state changes itself.",
                DeprecationWarning, stacklevel=2,
            )
        event.setdefault("ts", time.time())
        self._buffer.append(event)
        if len(self._buffer) >= self.batch_size:
            self.flush()

    def flush(self) -> dict | None:
        """Flushes both the legacy event buffer and the observation buffer.
        Returns the legacy /events response (or None if that buffer was empty)
        for backward compatibility; check flush_observations() separately if
        you need that response too."""
        result = None
        if self._buffer:
            batch, self._buffer = self._buffer, []
            result = self._req("POST", "/events", {"job_id": self.job_id, "events": batch})
        self.flush_observations()
        return result

    def post_events(self, events: list[dict], job_id: int | None = None) -> dict:
        return self._req("POST", "/events", {"job_id": job_id or self.job_id, "events": events})

    # ---------- geometry ----------
    def project(self, source: dict, points_px: list[tuple]) -> list[tuple]:
        """Pixel -> map meters using the source's stored homography (local, no HTTP)."""
        H = (source.get("calibration") or {}).get("H")
        if not H:
            raise RuntimeError(f"source {source['id']} is not calibrated")
        out = []
        for (x, y) in points_px:
            w = H[2][0] * x + H[2][1] * y + H[2][2]
            out.append(((H[0][0] * x + H[0][1] * y + H[0][2]) / w,
                        (H[1][0] * x + H[1][1] * y + H[1][2]) / w))
        return out

    def project_remote(self, source_id: int, points: list[dict],
                       surface_id: int | None = None) -> list[dict]:
        return self._req("POST", f"/sources/{source_id}/project",
                         {"points": points, "surface_id": surface_id})["points"]

    def unproject_remote(self, source_id: int, points: list[dict],
                         surface_id: int | None = None) -> list[dict]:
        return self._req("POST", f"/sources/{source_id}/unproject",
                         {"points": points, "surface_id": surface_id})["points"]

    @staticmethod
    def point_in_zone(zone: dict, x: float, y: float) -> bool:
        poly, inside, j = zone["polygon"], False, len(zone["polygon"]) - 1
        for i in range(len(poly)):
            xi, yi, xj, yj = poly[i]["x"], poly[i]["y"], poly[j]["x"], poly[j]["y"]
            if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
                inside = not inside
            j = i
        return inside

    # ---------- video ----------
    def open_capture(self, source: dict, local_connection=None):
        """Open a camera in this worker process; StoreLens never proxies the feed.

        Resolution precedence is explicit local_connection, a StoreLens-managed
        connection, then an external local_secret_ref environment variable.
        """
        import cv2
        if local_connection is not None:
            if source["kind"] == "webcam" and str(local_connection).isdigit():
                local_connection = int(local_connection)
            return cv2.VideoCapture(local_connection)
        management = source.get("connection_management", "external_secret")
        if management == "storelens_managed":
            public_connection = source.get("connection") or {}
            if source.get("kind") == "webcam" and "device_index" in public_connection:
                return cv2.VideoCapture(int(public_connection["device_index"]))
            resolved = self.get_source_connection(int(source["id"]))
            connection = resolved.get("connection") or {}
            kind = resolved.get("kind", source.get("kind"))
            try:
                if kind == "webcam":
                    target = int(connection["device_index"])
                elif kind == "file":
                    target = connection["path"]
                elif kind == "rtsp":
                    scheme = connection.get("scheme", "rtsp")
                    auth = ""
                    if connection.get("username"):
                        auth = quote(connection["username"], safe="")
                        if connection.get("password") is not None:
                            auth += ":" + quote(connection["password"], safe="")
                        auth += "@"
                    path = quote(connection.get("path", "/"), safe="/?&=%")
                    target = f"{scheme}://{auth}{connection['host']}:{int(connection.get('port', 554))}{path}"
                elif kind == "http":
                    target = connection["url"]
                    if connection.get("auth_type") == "basic" and connection.get("username"):
                        parsed = urlsplit(target)
                        userinfo = quote(connection["username"], safe="")
                        if connection.get("password") is not None:
                            userinfo += ":" + quote(connection["password"], safe="")
                        target = urlunsplit((parsed.scheme, f"{userinfo}@{parsed.netloc}", parsed.path,
                                             parsed.query, parsed.fragment))
                else:
                    raise RuntimeError(f"managed capture is not supported for source kind {kind}")
            except (KeyError, TypeError, ValueError) as exc:
                raise RuntimeError(
                    f"source {source.get('id')} ({kind}) managed connection is incomplete"
                ) from exc
            return cv2.VideoCapture(target)

        locator = source.get("locator") or {}
        ref = locator.get("local_secret_ref")
        if ref:
            target = os.environ.get(ref)
            if target is None:
                raise RuntimeError(f"external source reference {ref} is not configured on this worker")
            if source.get("kind") == "webcam" and str(target).isdigit():
                target = int(target)
            return cv2.VideoCapture(target)
        # Backward compatibility for old safe webcam locators.
        if source.get("kind") == "webcam" and "device_index" in locator:
            return cv2.VideoCapture(int(locator["device_index"]))
        raise RuntimeError("source connection is not configured")


class CentroidTracker:
    """Small greedy nearest-centroid tracker: good enough to get stable track_ids from
    per-frame detections without heavyweight dependencies."""

    def __init__(self, max_distance: float = 80.0, max_missed: int = 15):
        self.max_distance = max_distance
        self.max_missed = max_missed
        self._next = 1
        self.tracks: dict[str, dict] = {}  # id -> {cx, cy, missed}

    def update(self, centroids: list[tuple]) -> list[tuple]:
        """centroids: [(cx, cy), ...] this frame. Returns [(track_id, cx, cy), ...]."""
        assigned = {}
        free = set(self.tracks)
        for c in centroids:
            best, best_d = None, self.max_distance
            for tid in free:
                t = self.tracks[tid]
                d = ((t["cx"] - c[0]) ** 2 + (t["cy"] - c[1]) ** 2) ** 0.5
                if d < best_d:
                    best, best_d = tid, d
            if best is not None:
                free.discard(best)
                assigned[best] = c
            else:
                tid = f"t{self._next}"
                self._next += 1
                assigned[tid] = c
        for tid in list(self.tracks):
            if tid not in assigned:
                self.tracks[tid]["missed"] += 1
                if self.tracks[tid]["missed"] > self.max_missed:
                    del self.tracks[tid]
        out = []
        for tid, (cx, cy) in assigned.items():
            self.tracks[tid] = {"cx": cx, "cy": cy, "missed": 0}
            out.append((tid, cx, cy))
        return out


def parse_args_base(description: str):
    """Shared CLI args for worker scripts."""
    import argparse
    ap = argparse.ArgumentParser(description=description)
    ap.add_argument("--url", default="http://localhost:8000", help="StoreLens base URL")
    ap.add_argument("--api-key", default="", help="X-API-Key if the server requires one")
    ap.add_argument("--source", type=int, required=False, help="source id to analyse")
    ap.add_argument(
        "--connection",
        default=os.environ.get("STORELENS_SOURCE_CONNECTION"),
        help="Explicit worker-local URL/path/index override; external-secret deployments may use STORELENS_SOURCE_CONNECTION",
    )
    return ap
