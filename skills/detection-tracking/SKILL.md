---
name: detection-tracking
description: Use for spatial traffic heatmaps, popularity/activity maps, presence counts, and time-in-zone questions ("dwell at checkout", "queue time", "how long at the promo stand", optionally split by an attribute like gender or staff/customer). Covers people/object detection and tracking with pixel or map evidence. The single recipe behind heatmap, presence, visit, and dwell analyses — they are all the same worker contract (submit tracked detections), differing only in the question asked of the resulting data.
---

# Detection & tracking — presence, heatmaps, visits, and dwell

Use for any question answered by "where were people/objects, and for how long."
StoreLens derives heatmaps, presence counts, zone visits, dwell duration, and
zone-to-zone flow **all from the same stream of tracked `detection` observations** —
there is no separate "dwell worker" or "heatmap worker" contract. What differs is only
which saved analysis you create afterward.

## What the platform needs from you

A stream of `detection` observations with a stable `entity_id` and spatial evidence.
For standing floor traffic, use the person's **feet** (`geometry.point_px`, or let
StoreLens pick foot/ankle keypoints or the bbox bottom-center — see precedence below).
Post 1–2 per second per entity for ordinary spatial analytics; don't post every
camera frame at 30fps. For zero-capable 0.5-second presence windows, run inference
at 4 Hz or faster and call `submit_detection_frame` once per sample, including
empty frames, with the same timestamp as that sample's detections. Missing frame
markers mean unknown, never zero.

Never send `zone_id`/`zone`, and never emit an enter/exit pair or a computed dwell value
yourself — StoreLens assigns the zone from geometry and derives visits/dwell/flow from
the raw stream (a run of same-zone detections for one entity becomes a visit once it has
enough confirmed samples; a gap or a zone change closes it).

Representative-point precedence, most to least preferred: explicit `point_px`, then
foot/ankle keypoints, then bbox bottom-center, then left empty if only a mask is given.

## Steps

1. `get_store_map()` — confirm which cameras are **placed + calibrated**. An uncalibrated
   camera's detections still get stored, but without a map point they can't contribute to
   a heatmap or be zone-assigned; ask the user to calibrate (Setup → Space & zones → ⌗) or
   proceed pixel-only if the question doesn't need zones.
2. Capture a frame from each candidate directly on the worker device — check the view
   actually covers the area the user cares about. Inspect `list_projection_surfaces` and
   `list_zone_views` when subjects are sitting, lying, occluded, or elevated (see the
   `geometry-calibration` skill).
3. `register_job("<question> – <scope>", event_types=["detection"], source_ids=[...])`.
4. Detect entities per frame. Model choice, best first:
   - `ultralytics` YOLO (`model.predict`, class 0 = person) if installed/installable;
   - OpenCV HOG person detector;
   - background subtraction blobs (always works, see template) — fine for both heatmaps and dwell.
5. Track with `storelens.CentroidTracker` (or equivalent) so `entity_id` is stable — this
   is what makes dwell/flow work, not a separate mechanism.
6. For an attribute split (e.g. "dwell by gender"), run a lightweight classifier per
   entity, majority-vote, cache per `entity_id`, and put the result in `attributes` —
   appearance-based attributes are estimates; say so in the job description and in any
   saved analysis's `question`.
7. Verify: `query_analytics("detection", ["active_entities"])` for presence,
   `query_analytics("detection", ["visits","average_dwell","total_dwell"], grouping={"primary":"zone"})`
   for dwell, `query_analytics("detection", ["density"])` for the heatmap.
8. Publish it: `create_analysis(name, subject="detection", measures=[...], filters, grouping,
   presentation="heatmap_map"|"bar"|"table")` — one analysis per question; switching
   `presentation` later never needs a second record.

## Worker template

```python
import os, sys, time
sys.path.insert(0, "sdk/python")
from storelens import StoreLens, CentroidTracker
import cv2

sl = StoreLens(os.environ["STORELENS_URL"])
src = sl.source(SOURCE_ID)
job = sl.register_job("Presence – whole store", "tracked detections", source_ids=[src["id"]],
                      event_types=["detection", "measurement"])
sl.register_worker("detection-tracker", version="1")
cap = sl.open_capture(src)
bg = cv2.createBackgroundSubtractorMOG2(history=300, varThreshold=32, detectShadows=False)
tracker = CentroidTracker(max_distance=90)

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
    sample_ts = time.time()
    tracks = tracker.update(feet)
    for tid, cx, cy in tracks:
        sl.submit_detection(source_id=src["id"], entity_id=tid, point_px=(cx, cy),
                            entity_type="person", ts=sample_ts)
    sl.submit_detection_frame(source_id=src["id"], entity_type="person",
                              count=len(tracks), ts=sample_ts)
    sl.flush()
sl.flush()
```

Swap the detection block for YOLO when available:
```python
from ultralytics import YOLO
model = YOLO("yolov8n.pt")
boxes = model.predict(frame, classes=[0], verbose=False)[0].boxes.xyxy.tolist()
for tid, (x0, y0, x1, y1) in zip(track_ids, boxes):
    sl.submit_detection(source_id=src["id"], entity_id=tid, bbox_px=(x0, y0, x1, y1))
```

## Pitfalls

- `entity_id` must be stable across frames or every dwell/flow measure collapses toward
  zero — this is the single most common cause of "dwell shows almost nothing."
- Feet, not bbox center — projecting a torso point through a floor homography lands
  meters off. For a lying person, use a named mattress/table plane, not a subtracted height.
- Post the original `bbox_px`/`keypoints_px` when available; StoreLens preserves them for
  review and can use them for camera zone-view membership rules.
- Don't flood: 30 fps × N entities adds nothing useful; 1–2 Hz is visually identical and
  is what StoreLens's visit-confirmation (min-samples, gap tolerance) assumes.
- Multiple cameras: one job is fine; submit with each observation's own `source_id`.
- Attribute keys become Analytics split-by options automatically; keep values short and
  consistent (`female`/`male`, not free text).
