"""Precompute the optional NVIDIA four-camera replay fixture.

This command is intentionally offline tooling. It needs Ultralytics, YOLO11n,
OpenCV, and a CUDA-capable PyTorch install; the StoreLens demo runtime does not.
No source frames or weights are written to the repository fixture.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
from pathlib import Path


CAMERAS = [f"Warehouse_Synthetic_Cam{i:03d}" for i in range(1, 5)]


def generate(dataset: Path, model_path: Path, output: Path, sample_fps: float) -> None:
    import cv2
    import numpy as np
    import torch
    import ultralytics
    from ultralytics import YOLO

    random.seed(0); np.random.seed(0); torch.manual_seed(0)
    videos = [dataset / "videos" / f"{camera}.mp4" for camera in CAMERAS]
    missing = [str(path) for path in videos if not path.is_file()]
    if missing:
        raise SystemExit(f"missing camera videos: {missing}")
    models = [YOLO(str(model_path)) for _ in videos]
    captures = [cv2.VideoCapture(str(path)) for path in videos]
    fps = [capture.get(cv2.CAP_PROP_FPS) for capture in captures]
    frames = [int(capture.get(cv2.CAP_PROP_FRAME_COUNT)) for capture in captures]
    if not all(value > 0 for value in fps) or len(set(round(value, 6) for value in fps)) != 1:
        raise SystemExit("camera videos must have one valid shared frame rate")
    if len(set(frames)) != 1:
        raise SystemExit("camera videos must have one shared frame count")
    step = max(1, round(fps[0] / sample_fps))
    header = {
        "type": "fixture_metadata", "schema_version": 1,
        "producer": {"detector": "yolo11n", "tracker": "ByteTrack", "device": "CUDA",
                     "ultralytics_version": ultralytics.__version__,
                     "model_sha256": hashlib.sha256(model_path.read_bytes()).hexdigest()},
        "dataset": "NVIDIA DeepStream MV3DT mtmc_4cam synthetic warehouse sample",
        "source_scope": "anonymous source-local tracks",
        "fps": fps[0], "frame_count": frames[0], "processed_stride": step,
        "duration_s": frames[0] / fps[0], "camera_keys": CAMERAS,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(header, sort_keys=True) + "\n")
        for frame_index in range(0, frames[0], step):
            for capture in captures:
                capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
            for camera_index, (camera, capture, model) in enumerate(zip(CAMERAS, captures, models)):
                ok, frame = capture.read()
                if not ok:
                    raise RuntimeError(f"{camera}: could not decode frame {frame_index}")
                result = model.track(
                    frame, persist=True, tracker="bytetrack.yaml", classes=[0], conf=0.25,
                    imgsz=960, device=0, half=True, verbose=False,
                )[0]
                detections = []
                boxes = result.boxes
                if boxes is not None:
                    xyxy = boxes.xyxy.detach().cpu().tolist()
                    confidences = boxes.conf.detach().cpu().tolist()
                    track_ids = boxes.id.detach().cpu().tolist() if boxes.id is not None else [None] * len(xyxy)
                    for box, confidence, track_id in zip(xyxy, confidences, track_ids):
                        x0, y0, x1, y1 = [round(float(value), 4) for value in box]
                        detections.append({
                            "local_track_id": None if track_id is None else str(int(track_id)),
                            "confidence": round(float(confidence), 6),
                            "bbox_px": [x0, y0, x1, y1],
                            "point_px": [round((x0 + x1) / 2, 4), y1],
                        })
                record = {
                    "type": "source_frame", "schema_version": 1,
                    "source_key": camera, "frame_index": frame_index,
                    "video_time_s": round(frame_index / fps[camera_index], 6),
                    "detection_frame_count": len(detections),
                    "detections": detections,
                }
                stream.write(json.dumps(record, sort_keys=True) + "\n")
    for capture in captures:
        capture.release()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True,
                        help="Path to the extracted datasets/mtmc_4cam directory")
    parser.add_argument("--model", type=Path, required=True, help="Local yolo11n.pt path")
    parser.add_argument("--output", type=Path,
                        default=Path(__file__).parent / "fixtures" / "nvidia_mv3dt_yolo11n_bytetrack.jsonl")
    parser.add_argument("--sample-fps", type=float, default=1.0)
    args = parser.parse_args()
    generate(args.dataset, args.model, args.output, args.sample_fps)


if __name__ == "__main__":
    main()
