"""Deterministic high-rate load harness for the realtime pipeline.

Starts a real uvicorn server in its own process against a throwaway workspace,
then drives it with synthetic cameras over real HTTP. Nothing in the server path
is mocked — real middleware, validation, enrichment, SQLite, live scheduler — and
the load generator does not share the server's interpreter, so measured latency
is the server's, not the harness's.

    python scripts/load_test_realtime.py --cameras 4 --fps 60 --duration 30

Freshness, not throughput, is the objective. The report distinguishes:

* ingestion latency  — how long a worker waits for its POST
* fusion tick        — how long one group tick costs
* source-to-combined — how long after a sample is accepted its evidence is
                       reflected in fused current state
* coalesced updates  — live updates superseded before a tick consumed them.
                       Their raw rows are still in `events`, and the harness
                       verifies that by counting stored samples against sent ones.

Scenarios: `--scenario steady|asymmetric|stop-camera|overload`.
"""
from __future__ import annotations

import argparse
import http.client
import json
import math
import os
import shutil
import socket
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

ZONE_POLYGON = [{"x": 4.0, "y": 4.0}, {"x": 16.0, "y": 4.0},
                {"x": 16.0, "y": 12.0}, {"x": 4.0, "y": 12.0}]
FRAME_W, FRAME_H = 1920, 1080
# 1920 px across 20 m, 1080 px across 14 m — a plain scaling homography, so a
# projected coordinate is checkable by hand.
CALIBRATION_POINTS = [
    {"px": {"x": 0, "y": 0}, "map": {"x": 0, "y": 0}},
    {"px": {"x": FRAME_W, "y": 0}, "map": {"x": 20, "y": 0}},
    {"px": {"x": FRAME_W, "y": FRAME_H}, "map": {"x": 20, "y": 14}},
    {"px": {"x": 0, "y": FRAME_H}, "map": {"x": 0, "y": 14}},
]
ASYMMETRIC_FPS = [60.0, 30.0, 60.0, 15.0]


def percentiles(values: list[float]) -> dict:
    if not values:
        return {"count": 0}
    ordered = sorted(values)

    def at(fraction: float) -> float:
        index = min(len(ordered) - 1, max(0, round(fraction * len(ordered) + 0.5) - 1))
        return round(ordered[index] * 1000.0, 3)

    return {"count": len(ordered), "p50": at(0.5), "p95": at(0.95), "p99": at(0.99),
            "max": round(ordered[-1] * 1000.0, 3),
            "mean": round(statistics.fmean(ordered) * 1000.0, 3)}


def person_position(person: int, people: int, elapsed: float) -> tuple[float, float]:
    """A deterministic walk that keeps people moving in and out of the zone."""
    phase = 2.0 * math.pi * (person / max(people, 1))
    x = 10.0 + 7.0 * math.sin(0.35 * elapsed + phase)
    y = 7.0 + 4.5 * math.cos(0.27 * elapsed + phase * 1.7)
    return x, y


def detections_for(camera: int, people: int, elapsed: float) -> list[dict]:
    """Every camera sees the same people, offset slightly — overlapping views."""
    rows = []
    for person in range(people):
        x, y = person_position(person, people, elapsed)
        # A small per-camera offset well inside a 1.5 m spatial gate, so the
        # group has real cross-camera association work to do.
        x += 0.12 * math.sin(camera + person)
        y += 0.12 * math.cos(camera + person)
        px = max(1.0, min(FRAME_W - 1.0, x * FRAME_W / 20.0))
        py = max(1.0, min(FRAME_H - 1.0, y * FRAME_H / 14.0))
        rows.append({
            "entity_id": f"c{camera}-p{person}",
            "label": "person",
            "confidence": 0.9,
            "bbox_px": [px - 30, py - 120, px + 30, py],
            "identity_scope": "source",
        })
    return rows


class Client:
    """One keep-alive HTTP connection. Each camera thread owns one."""

    def __init__(self, port: int) -> None:
        self.port = port
        self.connection = http.client.HTTPConnection("127.0.0.1", port, timeout=30)

    def call(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
        payload = None if body is None else json.dumps(body).encode()
        headers = {"Content-Type": "application/json"} if payload else {}
        for attempt in (0, 1):
            try:
                self.connection.request(method, path, payload, headers)
                response = self.connection.getresponse()
                raw = response.read()
                break
            except (http.client.HTTPException, OSError):
                # An idle keep-alive connection can be reaped by the server.
                # Reconnecting once is transport hygiene, not a retried request:
                # a request that reached the server would have got a response.
                self.connection.close()
                self.connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
                if attempt:
                    raise
        try:
            return response.status, json.loads(raw or b"null")
        except ValueError:
            return response.status, {"raw": raw[:200].decode("utf-8", "replace")}

    def close(self) -> None:
        self.connection.close()


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class Server:
    def __init__(self, workspace: str, tick_hz: float, duration: float) -> None:
        self.workspace = workspace
        self.port = free_port()
        environment = {
            **os.environ,
            "STORELENS_DATA": workspace,
            "STORELENS_API_KEY": "",
            "STORELENS_LIVE_TICK_INTERVAL_S": str(1.0 / tick_hz),
            # Keep the periodic ongoing-alert poller out of the measurement.
            "STORELENS_ALERT_POLL_INTERVAL_S": str(max(duration * 4, 120)),
            "PYTHONPATH": str(ROOT),
        }
        self.process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "server.app:app", "--host", "127.0.0.1",
             "--port", str(self.port), "--log-level", "warning", "--workers", "1"],
            cwd=str(ROOT), env=environment,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    def wait_until_ready(self, timeout: float = 60.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(f"server exited early:\n{self.process.stdout.read()}")
            try:
                client = Client(self.port)
                status, _ = client.call("GET", "/api/v1/health")
                client.close()
                if status == 200:
                    return
            except OSError:
                pass
            time.sleep(0.1)
        raise RuntimeError("server did not become ready")

    def stop(self) -> None:
        self.process.terminate()
        try:
            self.process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=10)


class Harness:
    def __init__(self, args) -> None:
        self.args = args
        self.workspace = tempfile.mkdtemp(prefix="storelens-loadtest-")
        self.latencies: dict[int, list[float]] = {}
        self.submitted: dict[int, int] = {}
        self.errors: list[str] = []
        self.lock = threading.Lock()
        self.wall_start = 0.0

    def setup(self, client: Client) -> dict:
        source_ids = []
        for camera in range(self.args.cameras):
            status, source = client.call("POST", "/api/v1/sources",
                                         {"name": f"Load cam {camera + 1}", "kind": "http"})
            assert status == 201, source
            status, _ = client.call("PUT", f"/api/v1/sources/{source['id']}/calibration",
                                    {"points": CALIBRATION_POINTS,
                                     "frame_w": FRAME_W, "frame_h": FRAME_H})
            assert status == 200
            source_ids.append(source["id"])
        client.call("PUT", "/api/v1/store",
                    {"name": "Load test space", "width_m": 20, "height_m": 14})
        status, zone = client.call("POST", "/api/v1/zones",
                                   {"name": "Load aisle", "ztype": "aisle", "polygon": ZONE_POLYGON})
        assert status == 201, zone
        status, group = client.call("POST", "/api/v1/multiview/groups", {
            "name": "Load group", "source_ids": source_ids, "time_tolerance_s": 0.75,
            "spatial_gate_m": 1.5, "track_age_s": 2.0})
        assert status == 201, group
        return {"source_ids": source_ids, "zone_id": zone["id"], "group_id": group["id"]}

    def camera_fps(self, camera: int) -> float:
        if self.args.scenario == "asymmetric":
            return ASYMMETRIC_FPS[camera % len(ASYMMETRIC_FPS)]
        return float(self.args.fps)

    def camera_duration(self, camera: int) -> float:
        if self.args.scenario == "stop-camera" and camera == self.args.cameras - 1:
            return self.args.duration / 2.0
        return float(self.args.duration)

    def run_camera(self, port: int, camera: int, source_id: int, start: float) -> None:
        client = Client(port)
        period = 1.0 / self.camera_fps(camera)
        duration = self.camera_duration(camera)
        latencies: list[float] = []
        submitted = 0
        frame = 0
        deadline = start
        while time.monotonic() - start < duration:
            now = time.monotonic()
            if deadline > now:
                time.sleep(deadline - now)
            body = {
                "schema_version": 2, "source_id": source_id,
                "sample_id": f"cam{camera}-f{frame}",
                "timestamp": self.wall_start + (deadline - start),
                "frame_index": frame, "entity_type": "person",
                "detections": detections_for(camera, self.args.people, deadline - start),
            }
            sent_at = time.perf_counter()
            try:
                status, payload = client.call("POST", "/api/v1/detection-samples", body)
                if status != 200:
                    with self.lock:
                        self.errors.append(f"cam{camera} frame{frame}: {status} {payload}")
                else:
                    submitted += 1
            except Exception as exc:  # noqa: BLE001 - reported, never swallowed
                with self.lock:
                    self.errors.append(f"cam{camera} frame{frame}: {exc!r}")
                client = Client(port)
            latencies.append(time.perf_counter() - sent_at)
            frame += 1
            # Monotonic pacing: a slow response must not shift the whole schedule,
            # and a backlog of frames must not be replayed in a burst.
            deadline += period
            if deadline < time.monotonic():
                deadline = time.monotonic()
        client.close()
        with self.lock:
            self.latencies[camera] = latencies
            self.submitted[camera] = submitted

    def overload_pressure(self, port: int, stop: threading.Event) -> None:
        """Make live computation temporarily exceed the tick budget.

        A legitimate expensive read of fused state, not a sleep: the scheduler
        really has to contend for the same work and the same SQLite writer.
        """
        client = Client(port)
        while not stop.is_set():
            try:
                client.call("GET", "/api/v1/multiview/current")
            except Exception:  # noqa: BLE001 - pressure generator, not a measurement
                client = Client(port)
        client.close()

    def run(self) -> dict:
        server = Server(self.workspace, self.args.tick_hz, self.args.duration)
        try:
            server.wait_until_ready()
            control = Client(server.port)
            setup = self.setup(control)
            _status, health = control.call("GET", "/api/v1/health")
            self.wall_start = health["ts"]
            control.call("POST", "/api/v1/realtime/metrics/reset")
            start = time.monotonic()
            stop = threading.Event()
            threads = [threading.Thread(
                target=self.run_camera,
                args=(server.port, camera, setup["source_ids"][camera], start),
                name=f"camera-{camera}") for camera in range(self.args.cameras)]
            pressure = None
            if self.args.scenario == "overload":
                pressure = threading.Thread(target=self.overload_pressure,
                                            args=(server.port, stop), daemon=True)
                pressure.start()
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            wall = time.monotonic() - start
            stop.set()
            if pressure is not None:
                pressure.join(timeout=5)
            _status, lag = control.call("GET", "/api/v1/realtime/metrics")
            # Reading fused state drains anything the scheduler has not run.
            _status, current = control.call(
                "GET", f"/api/v1/multiview/current?group_id={setup['group_id']}")
            _status, occupancy = control.call(
                "GET", f"/api/v1/multiview/occupancy?group_id={setup['group_id']}"
                       f"&zone_id={setup['zone_id']}")
            _status, metrics = control.call("GET", "/api/v1/realtime/metrics")
            control.close()
        finally:
            server.stop()
        history = self.history_report(setup)
        return self.report(wall, lag["coordinator"]["oldest_unconsumed_live_update_s"],
                           metrics, current, occupancy, history, setup)

    def history_report(self, setup: dict) -> dict:
        """Count durable raw evidence by reading the workspace file directly."""
        con = sqlite3.connect(os.path.join(self.workspace, "storelens.db"))
        con.row_factory = sqlite3.Row
        try:
            markers = {row["source_id"]: row["n"] for row in con.execute(
                "SELECT source_id, COUNT(*) n FROM events WHERE event_type='measurement' "
                "AND name='detection_frame_count' GROUP BY source_id")}
            detections = con.execute(
                "SELECT COUNT(*) n FROM events WHERE event_type='detection'").fetchone()["n"]
            distinct = con.execute(
                "SELECT COUNT(*) n FROM (SELECT DISTINCT source_id, sample_id FROM events "
                "WHERE sample_id IS NOT NULL)").fetchone()["n"]
            mismatched = [dict(row) for row in con.execute(
                "SELECT m.source_id, m.sample_id, m.value expected, COUNT(d.id) observed "
                "FROM events m LEFT JOIN events d ON d.source_id=m.source_id "
                "AND d.sample_id=m.sample_id AND d.event_type='detection' "
                "WHERE m.event_type='measurement' AND m.name='detection_frame_count' "
                "GROUP BY m.source_id, m.sample_id HAVING observed != expected LIMIT 5")]
            fused_history = con.execute(
                "SELECT COUNT(*) n FROM fused_observations").fetchone()["n"]
        finally:
            con.close()
        return {
            "stored_completion_markers": markers,
            "stored_detection_rows": detections,
            "distinct_stored_samples": distinct,
            "samples_with_mismatched_detection_count": mismatched,
            "fused_observation_rows": fused_history,
        }

    def report(self, wall, lag, metrics, current, occupancy, history, setup) -> dict:
        all_latencies = [value for values in self.latencies.values() for value in values]
        submitted_total = sum(self.submitted.values())
        stored_total = sum(history["stored_completion_markers"].values())
        counters = metrics["counters"]
        durations = metrics["durations_ms"]
        ticks = counters.get("realtime.ticks_executed", 0)
        expected_input = sum(
            self.camera_fps(camera) * self.camera_duration(camera)
            for camera in range(self.args.cameras))
        return {
            "scenario": self.args.scenario,
            "configuration": {
                "cameras": self.args.cameras, "target_fps": self.args.fps,
                "people_per_camera": self.args.people, "duration_s": self.args.duration,
                "max_tick_hz": self.args.tick_hz,
                "target_samples_per_s": round(expected_input / self.args.duration, 1),
            },
            "input": {
                "wall_clock_s": round(wall, 3),
                "samples_accepted": submitted_total,
                "sustained_samples_per_s": round(submitted_total / wall, 1) if wall else 0,
                "per_camera_accepted": dict(sorted(self.submitted.items())),
                "errors": self.errors[:10],
                "error_count": len(self.errors),
                "batches_run_inline": counters.get("ingestion.inline_batches", 0),
                "batches_offloaded_to_pipeline_thread":
                    counters.get("ingestion.offloaded_batches", 0),
            },
            "history": {
                **history,
                "durable_samples_total": stored_total,
                "raw_evidence_dropped": max(0, submitted_total - stored_total),
                "all_accepted_samples_durable":
                    stored_total == submitted_total
                    and not history["samples_with_mismatched_detection_count"],
            },
            "live_scheduler": {
                "ticks_requested": counters.get("realtime.ticks_requested", 0),
                "ticks_executed": ticks,
                "ticks_executed_per_s": round(ticks / wall, 1) if wall else 0,
                "ticks_skipped_clean": counters.get("realtime.ticks_skipped_clean", 0),
                "deadlines_missed_and_dropped": counters.get("realtime.deadlines_missed", 0),
                "live_updates_coalesced": counters.get("realtime.live_updates_coalesced", 0),
                "live_updates_coalesced_per_s":
                    round(counters.get("realtime.live_updates_coalesced", 0) / wall, 1) if wall else 0,
                "source_updates": counters.get("realtime.source_updates", 0),
                "fusion_source_stages": counters.get("fusion.source_stages", 0),
                "tick_errors": counters.get("realtime.tick_errors", 0),
                "max_live_state_lag_s_at_end_of_run": lag,
                "dirty_groups_after_run": metrics["coordinator"]["dirty_groups"],
            },
            "latency_ms": {
                "ingestion_request_client_observed": percentiles(all_latencies),
                "ingestion_endpoint_total": durations.get("ingestion.endpoint_duration_s", {}),
                "ingestion_processing": durations.get("ingestion.process_duration_s", {}),
                "raw_transaction": durations.get("ingestion.raw_transaction_s", {}),
                "fusion_tick": durations.get("realtime.tick_duration_s", {}),
                "fusion_stage_associate": durations.get("fusion.stage_associate_s", {}),
                "fusion_stage_refresh": durations.get("fusion.stage_refresh_s", {}),
                "source_to_combined_state": durations.get(
                    "realtime.source_to_combined_state_s", {}),
            },
            "final_state": {
                "fused_entities": len(current["entities"]),
                "group_quality": current["groups"][0]["quality"] if current["groups"] else None,
                "zone_occupancy": occupancy.get("value"),
                "zone_occupancy_quality": occupancy.get("quality"),
            },
        }

    def cleanup(self) -> None:
        shutil.rmtree(self.workspace, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cameras", type=int, default=4)
    parser.add_argument("--fps", type=float, default=60.0)
    parser.add_argument("--duration", type=float, default=30.0)
    parser.add_argument("--people", type=int, default=4)
    parser.add_argument("--tick-hz", type=float, default=100.0)
    parser.add_argument("--scenario", default="steady",
                        choices=["steady", "asymmetric", "stop-camera", "overload"])
    parser.add_argument("--keep-workspace", action="store_true")
    args = parser.parse_args()
    harness = Harness(args)
    try:
        report = harness.run()
    finally:
        if not args.keep_workspace:
            harness.cleanup()
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["history"]["all_accepted_samples_durable"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
