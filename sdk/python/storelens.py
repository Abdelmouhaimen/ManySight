"""StoreLens worker SDK — a tiny client for analysis scripts (the ones Codex writes).

Workers post raw observations (detections, zone_enter/zone_exit pairs, label-only
state_change flips, per-frame counts) — the platform derives dwell, durations, and
every insight. Never post computed aggregates (zone_dwell is deprecated/ignored).

Typical worker loop:
    from storelens import StoreLens, CentroidTracker
    sl = StoreLens("http://localhost:8000")
    src = sl.source(1)
    job = sl.register_job("Dwell at checkout", event_types=["detection", "zone_enter", "zone_exit"])
    worker = sl.register_worker("checkout-worker", version="1")
    cap = sl.open_capture(src)
    ...
    command = sl.heartbeat(metrics={"fps": fps})
    if command["should_stop"]:
        break
    sl.add_event(source_id=src["id"], event_type="detection", track_id=tid, point_px={"x": u, "y": v})
    sl.flush()   # or use `with sl.batch():` — events auto-flush every `batch_size`
"""
import atexit
import json
import time

import requests


class StoreLens:
    def __init__(self, base_url: str = "http://localhost:8000", api_key: str = "", batch_size: int = 200):
        self.base = base_url.rstrip("/") + "/api/v1"
        self.session = requests.Session()
        if api_key:
            self.session.headers["X-API-Key"] = api_key
        self.batch_size = batch_size
        self.job_id = None
        self.worker_instance_id = None
        self._buffer: list[dict] = []
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
        return self._req("GET", f"/sources/{source_id}", params={"secrets": "true"})

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

    def add_event(self, **event):
        """Buffer one event; flushes automatically at batch_size. See API docs for fields:
        ts, source_id, event_type, track_id, zone_id/zone, point_px/point_map/bbox,
        keypoints/mask, point_kind, projection_surface_id, zone_view_id, value, label,
        attributes."""
        event.setdefault("ts", time.time())
        self._buffer.append(event)
        if len(self._buffer) >= self.batch_size:
            self.flush()

    def flush(self) -> dict | None:
        if not self._buffer:
            return None
        batch, self._buffer = self._buffer, []
        return self._req("POST", "/events", {"job_id": self.job_id, "events": batch})

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
    def open_capture(self, source: dict):
        """cv2.VideoCapture for any source kind (webcam index, file path, rtsp/http URL)."""
        import cv2
        if source["kind"] == "webcam":
            return cv2.VideoCapture(int(source.get("url") or 0))
        return cv2.VideoCapture(source.get("connect_url") or source["url"])


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
    return ap
