"""Detect people and record whether each bounding box maps into the Bed zone.

The worker posts raw person detections to StoreLens. Each event contains the
bounding box, confidence, pixel-space overlap, and an explicit map point. For a
box overlapping the mattress ROI, the projected map y coordinate is corrected
for the elevated mattress before StoreLens performs map-zone assignment.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import cv2
import numpy as np


PROJECT_DIR = Path(__file__).resolve().parent
LOCAL_DEPS = PROJECT_DIR / ".deps"
TORCHVISION_COMPAT = PROJECT_DIR / ".torchvision_compat"
STORELENS_SDK = PROJECT_DIR.parents[1] / "sdk" / "python"
sys.path.insert(0, str(LOCAL_DEPS))
sys.path.insert(0, str(TORCHVISION_COMPAT))
sys.path.insert(0, str(STORELENS_SDK))

from storelens import CentroidTracker, StoreLens  # noqa: E402
from ultralytics import YOLO  # noqa: E402


BED_POLYGON = np.array(
    [(242, 184), (459, 144), (639, 150), (639, 291), (255, 294)],
    dtype=np.float32,
)

BED_FOOTPRINT_MAP = np.array(
    [(0.8, 2.58), (3.06, 2.63), (3.12, 3.93), (0.8, 3.97)],
    dtype=np.float32,
)


def bbox_polygon_overlap(box_xyxy: tuple[float, float, float, float]) -> tuple[float, float]:
    """Return intersection area and fraction of the person box inside the bed polygon."""
    x0, y0, x1, y1 = box_xyxy
    rect = np.array([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], dtype=np.float32)
    intersection_area, _ = cv2.intersectConvexConvex(BED_POLYGON, rect)
    box_area = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    ratio = float(intersection_area / box_area) if box_area else 0.0
    return float(intersection_area), ratio


def point_in_bed_footprint(point_map: tuple[float, float]) -> bool:
    return cv2.pointPolygonTest(BED_FOOTPRINT_MAP, point_map, False) >= 0


def annotate(frame, detections, bed_zone_id: int):
    overlay = frame.copy()
    cv2.fillPoly(overlay, [BED_POLYGON.astype(np.int32)], (229, 135, 57))
    cv2.addWeighted(overlay, 0.22, frame, 0.78, 0, frame)
    cv2.polylines(frame, [BED_POLYGON.astype(np.int32)], True, (229, 135, 57), 3)
    for detection in detections:
        x0, y0, x1, y1 = (round(v) for v in detection["xyxy"])
        color = (40, 190, 40) if detection["overlap"] else (40, 90, 230)
        cv2.rectangle(frame, (x0, y0), (x1, y1), color, 3)
        map_x, map_y = detection["point_map"]
        text = (
            f"person {detection['confidence']:.2f} | "
            f"bed overlap: {'YES' if detection['overlap'] else 'NO'} "
            f"({detection['overlap_ratio']:.1%}) | map {map_x:.2f},{map_y:.2f}"
        )
        cv2.putText(frame, text, (x0, max(22, y0 - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2)
    cv2.putText(
        frame,
        f"Bed zone {bed_zone_id}",
        (360, 220),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.65,
        (229, 135, 57),
        2,
    )


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--source", type=int, default=2)
    parser.add_argument("--zone-id", type=int, default=1)
    parser.add_argument("--job-id", type=int, required=True)
    parser.add_argument("--model", default="yolo11n.pt")
    parser.add_argument("--confidence", type=float, default=0.25)
    parser.add_argument(
        "--map-y-correction",
        type=float,
        default=0.55,
        help="metres subtracted from projected map y for mattress-overlapping boxes",
    )
    parser.add_argument("--fps", type=float, default=1.0, help="observation rate")
    parser.add_argument(
        "--duration",
        type=float,
        default=20.0,
        help="run time in seconds; use 0 to run continuously until stopped",
    )
    parser.add_argument("--output", default="last_result.jpg")
    return parser.parse_args()


def main():
    args = parse_args()
    model = YOLO(args.model)
    sl = StoreLens(args.url, args.api_key, batch_size=100)
    sl.job_id = args.job_id
    source = sl.source(args.source)
    cap = sl.open_capture(source)
    if not cap.isOpened():
        raise RuntimeError(f"could not open source {args.source}")

    tracker = CentroidTracker(max_distance=120, max_missed=8)
    run_id = f"run-{int(time.time())}"
    interval = 1.0 / max(args.fps, 0.1)
    started = time.monotonic()
    next_sample = started
    events_posted = 0
    frames_read = 0
    people_seen = 0
    overlaps_seen = 0
    latest_annotated = None

    try:
        while args.duration <= 0 or time.monotonic() - started < args.duration:
            ok, frame = cap.read()
            if not ok:
                time.sleep(0.05)
                continue
            frames_read += 1
            now_mono = time.monotonic()
            if now_mono < next_sample:
                continue
            next_sample = now_mono + interval

            result = model.predict(frame, classes=[0], conf=args.confidence, verbose=False)[0]
            boxes = result.boxes
            xyxy_rows = boxes.xyxy.cpu().tolist() if boxes is not None else []
            confidences = boxes.conf.cpu().tolist() if boxes is not None else []
            centroids = [((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0) for b in xyxy_rows]
            tracks = tracker.update(centroids)

            detections = []
            for index, (box, confidence) in enumerate(zip(xyxy_rows, confidences)):
                x0, y0, x1, y1 = (float(v) for v in box)
                intersection_area, overlap_ratio = bbox_polygon_overlap((x0, y0, x1, y1))
                pixel_overlaps_bed = intersection_area > 1.0
                anchor_px = ((x0 + x1) / 2.0, y1)
                raw_map_x, raw_map_y = sl.project(source, [anchor_px])[0]
                applied_y_correction = args.map_y_correction if pixel_overlaps_bed else 0.0
                corrected_map = (raw_map_x, raw_map_y - applied_y_correction)
                map_point_inside_bed = point_in_bed_footprint(corrected_map)
                overlaps_bed = pixel_overlaps_bed and map_point_inside_bed
                local_track_id = tracks[index][0] if index < len(tracks) else f"frame-{frames_read}-{index}"
                track_id = f"{run_id}-{local_track_id}"
                event = {
                    "source_id": args.source,
                    "event_type": "detection",
                    "track_id": track_id,
                    "label": "person",
                    "bbox": [x0, y0, x1 - x0, y1 - y0],
                    "point_px": {"x": anchor_px[0], "y": anchor_px[1]},
                    "point_map": {"x": corrected_map[0], "y": corrected_map[1]},
                    "attributes": {
                        "confidence": float(confidence),
                        "model": Path(args.model).name,
                        "run_id": run_id,
                        "bed_overlap": overlaps_bed,
                        "pixel_polygon_overlap": pixel_overlaps_bed,
                        "map_point_inside_bed": map_point_inside_bed,
                        "intersection_area_px": round(intersection_area, 2),
                        "bbox_overlap_ratio": round(overlap_ratio, 4),
                        "raw_point_map": {"x": round(raw_map_x, 4), "y": round(raw_map_y, 4)},
                        "map_y_correction_m": applied_y_correction,
                        "overlap_method": "pixel_bbox_overlap_and_corrected_map_point",
                    },
                }
                if overlaps_bed:
                    overlaps_seen += 1
                sl.add_event(**event)
                events_posted += 1
                people_seen += 1
                detections.append(
                    {
                        "xyxy": (x0, y0, x1, y1),
                        "confidence": float(confidence),
                        "overlap": overlaps_bed,
                        "overlap_ratio": overlap_ratio,
                        "point_map": corrected_map,
                    }
                )

            latest_annotated = frame.copy()
            annotate(latest_annotated, detections, args.zone_id)
    finally:
        cap.release()
        flush_result = sl.flush()
        if latest_annotated is not None:
            cv2.imwrite(str(PROJECT_DIR / args.output), latest_annotated)

    print(
        {
            "job_id": args.job_id,
            "frames_read": frames_read,
            "person_observations": people_seen,
            "bed_overlap_observations": overlaps_seen,
            "events_posted": events_posted,
            "flush_result": flush_result,
            "output": str(PROJECT_DIR / args.output),
        }
    )


if __name__ == "__main__":
    main()
