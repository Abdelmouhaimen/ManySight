"""Heatmap worker — person positions from a real video source.

Detects moving people (YOLO if ultralytics is installed, otherwise background
subtraction), tracks them, and submits one atomic `DetectionSample` per
processed frame with feet pixel points. The platform projects them through the
source's homography onto the floor plan and derives the heatmap, presence,
visits, and dwell itself — this worker never computes a heatmap or a zone.

It also demonstrates the three rates a tracking worker has to keep apart:

* the **source** delivers frames at its own rate;
* the **tracker** consumes every frame it can, because association quality
  depends on the gap between consecutive frames (target: >= 15 FPS);
* **submission** is gated separately and is normally slower.

Sleeping the capture loop down to the submission rate would starve the tracker
and cap a capable GPU at a few FPS, so it does not do that.

Usage:
    python examples/heatmap_tracker.py --source 1 [--url http://localhost:8000]
                                       [--submission-hz 5] [--device auto]
"""
import sys
import time

sys.path.insert(0, "sdk/python")
from manysight import (ManySight, CentroidTracker, SubmissionGate,  # noqa: E402
                       capture_fps, parse_args_base, probe_perception_runtime)


def yolo_detector(device: str, half: bool):
    from ultralytics import YOLO
    model = YOLO("yolov8n.pt")
    if device == "cuda":
        model.to("cuda")

    def detect(frame):
        # half= only on a validated CUDA path; FP16 on CPU is slower, not faster.
        boxes = model.predict(frame, classes=[0], verbose=False,
                              device=device, half=half)[0].boxes
        out = []
        for b in boxes.xyxy.tolist():
            x0, y0, x1, y1 = b[:4]
            out.append(((x0 + x1) / 2, y1))  # feet
        return out
    return detect


def motion_detector():
    import cv2
    bg = cv2.createBackgroundSubtractorMOG2(history=300, varThreshold=32, detectShadows=False)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))

    def detect(frame):
        mask = cv2.morphologyEx(bg.apply(frame), cv2.MORPH_OPEN, kernel)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        out = []
        for c in contours:
            if cv2.contourArea(c) < 800:
                continue
            x, y, w, h = cv2.boundingRect(c)
            out.append((x + w / 2, y + h))
        return out
    return detect


def submit_tracked_frame(sl, source_id, tracks, sample_ts, detector):
    """Submit one complete processed person frame as a single atomic envelope.

    No tracks is a real observed zero and is submitted exactly like any other
    frame — never as a fake detection, and never by simply staying silent.
    """
    sample = sl.begin_detection_sample(source_id, "person", ts=sample_ts,
                                       attributes={"detector": detector})
    for tid, cx, cy in tracks:
        sample.add_detection(entity_id=str(tid), point_px=(cx, cy), identity_scope="source")
    return sample.submit()


def choose_runtime(requested: str) -> dict:
    """Pick the device from the machine, not from an assumption."""
    probe = probe_perception_runtime()
    device = probe["recommended_device"] if requested == "auto" else requested
    half = bool(probe["fp16_supported"] and device == "cuda")
    print(f"runtime: device={device} fp16={half} "
          f"env={probe['environment']['kind']}:{probe['environment']['name']}")
    for note in probe["notes"]:
        print(f"  note: {note}")
    return {"device": device, "half": half}


def main():
    ap = parse_args_base(__doc__)
    ap.add_argument("--device", default="auto", choices=["auto", "cuda", "cpu"],
                    help="inference device (default: probe this machine)")
    ap.add_argument("--submission-hz", type=float, default=None,
                    help="central submission rate; default comes from the worker recipe")
    args = ap.parse_args()
    sl = ManySight(args.url, args.api_key)
    src = sl.source(args.source)
    if not src["calibrated"]:
        print("WARNING: source is not calibrated — observations keep pixel coords only and "
              "won't appear on the floor heatmap. Calibrate it in Setup → Cameras.")

    runtime = choose_runtime(args.device)
    try:
        detect = yolo_detector(runtime["device"], runtime["half"])
        print("using YOLOv8n person detector")
    except ImportError:
        detect = motion_detector()
        print("ultralytics not installed — using background-subtraction motion blobs")

    sl.register_job(f"Heatmap – {src['name']}", "person feet positions per processed frame",
                    source_ids=[src["id"]], event_types=["detection"])
    print("Contract: one atomic DetectionSample per processed frame, including empty ones — "
          "ManySight derives zones, presence, heatmaps, and dwell.")
    sl.register_worker("heatmap-tracker", version="2")
    cap = sl.open_capture(src, args.connection)

    # Ask the platform, not an old script, what rates this source deserves.
    source_fps = capture_fps(cap)
    plan = sl.worker_recipe(source_ids=[src["id"]],
                            source_fps=source_fps)["sampling"]["recommendation"]
    target_fps = plan["target_processing_fps"]
    gate = SubmissionGate(args.submission_hz or plan["target_submission_hz"])
    print(f"source_fps={source_fps or 'unknown'} target_processing_fps={target_fps} "
          f"submission_hz={round(1.0 / gate.interval, 2)}")
    for line in plan["rationale"]:
        print(f"  {line}")

    tracker = CentroidTracker(max_distance=90)
    last_heartbeat, window_start, processed, submitted, warned = 0.0, time.monotonic(), 0, 0, False
    processing_fps = submission_hz = None
    while True:
        ok, frame = cap.read()
        if not ok:
            print("stream ended / unreadable")
            break
        # Every decoded frame goes through the tracker: this is the rate that
        # decides whether track IDs survive, and it is not the submission rate.
        tracks = tracker.update(detect(frame))
        processed += 1
        if gate.due():
            submit_tracked_frame(sl, src["id"], tracks, time.time(), "heatmap_tracker")
            submitted += 1

        elapsed = time.monotonic() - window_start
        if elapsed >= 10:
            processing_fps = round(processed / elapsed, 2)
            submission_hz = round(submitted / elapsed, 2)
            window_start, processed, submitted = time.monotonic(), 0, 0
        now = time.time()
        if now - last_heartbeat >= 10:
            command = sl.heartbeat(metrics={
                "source_fps": source_fps, "processing_fps": processing_fps,
                "submission_hz": submission_hz, "device": runtime["device"],
                "precision": "fp16" if runtime["half"] else "fp32"})
            last_heartbeat = now
            if processing_fps and processing_fps < target_fps * 0.9 and not warned:
                warned = True
                # Say it out loud rather than letting arriving samples imply health.
                print(f"WARNING: processing {processing_fps} FPS against a {target_fps} FPS "
                      f"target on {runtime['device']}. Tracking quality is degraded — check "
                      "the device, the environment, the model size, and decode cost.")
            if command["should_stop"]:
                break
    sl.flush()
    sl.stop_worker()


if __name__ == "__main__":
    main()
