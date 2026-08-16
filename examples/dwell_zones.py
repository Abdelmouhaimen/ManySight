"""Dwell worker — tracked detections from a real video source.

Tracks people and submits one atomic `DetectionSample` per processed frame, with
feet pixel points and a stable per-track `entity_id` (including empty frames).
This worker never
resolves a zone, debounces a boundary crossing, or pairs an enter/exit — the
platform projects each point through the source calibration, matches it against
zone geometry with its own hysteresis rules, and derives visits and dwell
duration from the resulting stream of zoned detections (services/derive.py).
Requires a calibrated source so points can be projected onto zone polygons.

Dwell depends on visit *edges*, so the tracker runs on every decoded frame and
submission is gated separately — see the rate discussion in heatmap_tracker.py.

Usage:
    python examples/dwell_zones.py --source 1 [--zones "Checkout,Fridge"]
"""
import sys
import time

sys.path.insert(0, "sdk/python")
from manysight import (ManySight, CentroidTracker, SubmissionGate,  # noqa: E402
                       capture_fps, parse_args_base)
from heatmap_tracker import motion_detector, submit_tracked_frame  # noqa: E402


def main():
    ap = parse_args_base(__doc__)
    ap.add_argument("--zones", default="", help="comma-separated zone names, informational only")
    args = ap.parse_args()
    sl = ManySight(args.url, args.api_key)
    src = sl.source(args.source)
    if not src["calibrated"]:
        raise SystemExit("Source must be calibrated (Setup → Cameras) for dwell analysis.")
    zones = sl.zones()
    if args.zones:
        wanted = {z.strip().lower() for z in args.zones.split(",")}
        zones = [z for z in zones if z["name"].lower() in wanted]
    if not zones:
        print("Note: no matching zones on the map yet — detections still post; dwell/visits "
              "appear once zones are drawn in Setup → Space.")
    else:
        print(f"zones on this floor plan: {[z['name'] for z in zones]} "
              "(ManySight assigns them from geometry — this worker never resolves one itself)")

    sl.register_job(f"Dwell – {src['name']}", "tracked detections for dwell/visit derivation",
                    source_ids=[src["id"]], event_types=["detection"])
    sl.register_worker("dwell-zones", version="2")
    print("Contract: one atomic DetectionSample per processed frame, including empty ones; "
          "ManySight derives zone visits and dwell duration.")
    detect = motion_detector()
    cap = sl.open_capture(src, args.connection)

    source_fps = capture_fps(cap)
    plan = sl.worker_recipe(source_ids=[src["id"]],
                            source_fps=source_fps)["sampling"]["recommendation"]
    gate = SubmissionGate(plan["target_submission_hz"])
    tracker = CentroidTracker(max_distance=90)
    last_heartbeat, window_start, processed = 0.0, time.monotonic(), 0
    processing_fps = None

    while True:
        ok, frame = cap.read()
        if not ok:
            break
        # No sleep here: the tracker needs every frame it can get, and the gate
        # below — not a slower loop — is what keeps submission at the right rate.
        tracks = tracker.update(detect(frame))
        processed += 1
        if gate.due():
            submit_tracked_frame(sl, src["id"], tracks, time.time(), "dwell_zones")

        elapsed = time.monotonic() - window_start
        if elapsed >= 10:
            processing_fps = round(processed / elapsed, 2)
            window_start, processed = time.monotonic(), 0
        now = time.time()
        if now - last_heartbeat >= 10:
            command = sl.heartbeat(metrics={
                "source_fps": source_fps, "processing_fps": processing_fps,
                "submission_hz": plan["target_submission_hz"], "device": "cpu"})
            last_heartbeat = now
            if command["should_stop"]:
                break
    sl.flush()
    sl.stop_worker()


if __name__ == "__main__":
    main()
