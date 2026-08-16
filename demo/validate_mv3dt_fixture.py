"""Validate deterministic replay-fixture structure without model dependencies."""
from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path


def validate(path: Path) -> dict:
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not records or records[0].get("type") != "fixture_metadata":
        raise ValueError("first JSONL record must be fixture_metadata")
    metadata, frames = records[0], records[1:]
    cameras = metadata.get("camera_keys") or []
    if len(cameras) != 4 or len(set(cameras)) != 4:
        raise ValueError("fixture must declare four distinct camera keys")
    by_time: dict[float, set[str]] = defaultdict(set)
    previous = {camera: -1.0 for camera in cameras}
    for index, frame in enumerate(frames, 2):
        if frame.get("type") != "detection_sample" or frame.get("schema_version") != 2:
            raise ValueError(f"line {index}: invalid DetectionSample envelope")
        camera = frame.get("source_key")
        timestamp = frame.get("video_time_s")
        if camera not in cameras or not isinstance(timestamp, (int, float)) or not math.isfinite(timestamp):
            raise ValueError(f"line {index}: invalid source or video time")
        if timestamp <= previous[camera]:
            raise ValueError(f"line {index}: non-monotonic camera timeline")
        previous[camera] = timestamp
        by_time[float(timestamp)].add(camera)
        detections = frame.get("detections")
        expected_sample_id = f"{camera}-frame-{frame.get('frame_index')}"
        if frame.get("sample_id") != expected_sample_id:
            raise ValueError(f"line {index}: invalid source-local sample_id")
        if not isinstance(detections, list):
            raise ValueError(f"line {index}: detections must be a list (empty represents zero)")
        if "detection_frame_count" in frame:
            raise ValueError(f"line {index}: preferred DetectionSample must not author a completion measurement")
        for detection in detections:
            if set(detection) != {"local_track_id", "confidence", "bbox_px", "point_px"}:
                raise ValueError(f"line {index}: unexpected detection fields")
            if len(detection["bbox_px"]) != 4 or len(detection["point_px"]) != 2:
                raise ValueError(f"line {index}: malformed pixel geometry")
            if any(key in detection for key in ("zone_id", "zone", "fused_entity_id", "point_map")):
                raise ValueError(f"line {index}: fixture contains ManySight-derived fields")
    incomplete = [timestamp for timestamp, seen in by_time.items() if seen != set(cameras)]
    if incomplete:
        raise ValueError(f"incomplete synchronized timestamps: {incomplete[:5]}")
    return {"frames": len(frames), "timestamps": len(by_time), "cameras": cameras,
            "duration_s": metadata.get("duration_s")}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    print(json.dumps(validate(args.path), indent=2))


if __name__ == "__main__":
    main()
