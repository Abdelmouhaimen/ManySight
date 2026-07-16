# Heatmap — where do people actually go?

Use when the user asks for a **traffic/popularity heatmap** of the store (or one area).
Result: the Insights tab's "Store heatmap" lights up with position density projected
onto the floor plan.

## What the platform needs from you

A stream of `detection` events with a **pixel point at the person's feet**
(`point_px` = bottom-center of the bbox). If the source is calibrated the platform
projects pixels → floor meters and bins them; you don't do any geometry.

## Steps

1. `get_store_map()` — confirm which cameras are **placed + calibrated**. Uncalibrated
   cameras can't contribute to the heatmap; either ask the user to calibrate (Store Map
   tab → ⌗) or skip them.
2. `get_snapshot(source_id)` for each candidate — check the view actually covers the
   floor area the user cares about.
3. `register_job("Heatmap – <scope>", event_types=["detection"], source_ids=[...])`.
4. Detect people per frame. Model choice, best first:
   - `ultralytics` YOLO (`model.predict`, class 0 = person) if installed/installable;
   - OpenCV HOG person detector (`cv2.HOGDescriptor_getDefaultPeopleDetector`);
   - background subtraction blobs (always works, see template) — fine for heatmaps.
5. Track with `storelens.CentroidTracker` so `track_id` is stable (enables flow/occupancy too).
6. Post 1–2 detections per second per track (sample; don't post every frame at 30fps).
7. Verify: `get_analytics("heatmap", {"since": <start_ts>})` — expect >0 points; then tell
   the user to open Insights.

## Worker template

```python
import time, cv2, sys
sys.path.insert(0, "sdk/python")
from storelens import StoreLens, CentroidTracker

sl = StoreLens("http://localhost:8000")
src = sl.source(SOURCE_ID)
job = sl.register_job("Heatmap – whole store", "person detections for heatmap",
                      source_ids=[src["id"]], event_types=["detection"])
cap = sl.open_capture(src)
bg = cv2.createBackgroundSubtractorMOG2(history=300, varThreshold=32, detectShadows=False)
tracker = CentroidTracker(max_distance=90)
last_post = 0
while True:
    ok, frame = cap.read()
    if not ok:
        break
    mask = bg.apply(frame)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    feet = []
    for c in contours:
        if cv2.contourArea(c) < 800:            # ignore noise; tune per camera
            continue
        x, y, w, h = cv2.boundingRect(c)
        feet.append((x + w / 2, y + h))          # bottom-center = feet
    now = time.time()
    if now - last_post >= 0.5:                   # ≤2 Hz per track
        for tid, cx, cy in tracker.update(feet):
            sl.add_event(source_id=src["id"], event_type="detection",
                         track_id=tid, point_px={"x": cx, "y": cy})
        last_post = now
sl.flush()
```

Swap the detection block for YOLO when available:
```python
from ultralytics import YOLO
model = YOLO("yolov8n.pt")
boxes = model.predict(frame, classes=[0], verbose=False)[0].boxes.xywh
feet = [(float(x), float(y + h / 2)) for x, y, w, h in boxes]
```

## Pitfalls

- Feet, not bbox center — projecting a torso point through a floor homography lands meters off.
- Don't flood: 30 fps × N tracks kills nothing but adds nothing; 1–2 Hz is visually identical.
- Multiple cameras: one job is fine; post with each event's own `source_id`.
