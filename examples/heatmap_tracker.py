"""Heatmap worker — person positions from a real video source.

Detects moving people (YOLO if ultralytics is installed, otherwise background
subtraction), tracks them, and posts `detection` events with feet pixel points.
The platform projects them through the source's homography onto the floor plan.

Usage:
    python examples/heatmap_tracker.py --source 1 [--url http://localhost:8000] [--fps 2]
"""
import sys
import time

sys.path.insert(0, "sdk/python")
from storelens import StoreLens, CentroidTracker, parse_args_base  # noqa: E402


def yolo_detector():
    from ultralytics import YOLO
    model = YOLO("yolov8n.pt")

    def detect(frame):
        boxes = model.predict(frame, classes=[0], verbose=False)[0].boxes
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


def main():
    ap = parse_args_base(__doc__)
    ap.add_argument("--fps", type=float, default=2.0, help="event posting rate")
    args = ap.parse_args()
    sl = StoreLens(args.url, args.api_key)
    src = sl.source(args.source)
    if not src["calibrated"]:
        print("WARNING: source is not calibrated — events will keep pixel coords only "
              "and won't appear on the floor heatmap. Calibrate it in the Store Map tab.")
    try:
        detect = yolo_detector()
        print("using YOLOv8n person detector")
    except ImportError:
        detect = motion_detector()
        print("ultralytics not installed — using background-subtraction motion blobs")

    sl.register_job(f"Heatmap – {src['name']}", "person feet positions for spatial heatmap",
                    source_ids=[src["id"]], event_types=["detection"])
    sl.register_worker("heatmap-tracker", version="1")
    cap = sl.open_capture(src, args.connection)
    tracker = CentroidTracker(max_distance=90)
    interval, last, last_heartbeat = 1.0 / args.fps, 0.0, 0.0
    n = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            print("stream ended / unreadable")
            break
        feet = detect(frame)
        now = time.time()
        if now - last_heartbeat >= 10:
            command = sl.heartbeat(metrics={"detections": n})
            last_heartbeat = now
            if command["should_stop"]:
                break
        if now - last >= interval:
            for tid, cx, cy in tracker.update(feet):
                sl.add_event(source_id=src["id"], event_type="detection",
                             track_id=tid, point_px={"x": cx, "y": cy})
                n += 1
            last = now
            if n and n % 200 == 0:
                print(f"{n} detections posted")
    sl.flush()
    sl.stop_worker()


if __name__ == "__main__":
    main()
