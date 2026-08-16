"""Build the deterministic guided-demo derived replay cache through ManySight.

This developer command performs no model inference. It validates the prerecorded
source-local DetectionSample fixture, configures the real mapped workspace, and
runs selected 10 Hz samples through normal ManySight enrichment, multiview,
saved-query, and alert services. The resulting cache is playback data, not a
replacement for the normal live worker pipeline.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from demo.validate_mv3dt_fixture import validate
from server import db
from server.routers import multiview as multiview_router
from server.routers import queries
from server.routers.observations import (
    DetectionSampleIn,
    ObservationBatch,
    _process_observations,
    detection_sample_batch,
)
from server.services import alert_engine, demo_runtime, realtime


DEFAULT_RAW = ROOT / "demo" / "fixtures" / "nvidia_mv3dt_yolo11n_bytetrack.jsonl"
DEFAULT_RECIPE = ROOT / "demo" / "fixtures" / "nvidia_mv3dt_recipe.json"
DEFAULT_OUTPUT = ROOT / "demo" / "fixtures" / "nvidia_mv3dt_derived_replay.json"
DERIVATION_FILES = demo_runtime.DERIVATION_FILES
BASE_TIMESTAMP = 1_800_000_000.0


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_hash(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def derivation_hash() -> str:
    return demo_runtime.derivation_hash()


def load_raw(path: Path) -> tuple[dict, list[dict]]:
    validate(path)
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]
    return rows[0], rows[1:]


def validate_videos(asset_root: Path, metadata: dict) -> dict:
    import cv2

    details = {}
    for camera in metadata["camera_keys"]:
        path = asset_root / "videos" / f"{camera}.mp4"
        if not path.is_file():
            raise ValueError(f"missing demo video: {path}")
        capture = cv2.VideoCapture(str(path))
        try:
            if not capture.isOpened():
                raise ValueError(f"could not open demo video: {path}")
            fps = float(capture.get(cv2.CAP_PROP_FPS))
            frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        finally:
            capture.release()
        if abs(fps - float(metadata["fps"])) > .01 or frames < int(metadata["frame_count"]):
            raise ValueError(f"{camera}: video metadata does not match the raw fixture")
        details[camera] = {"fps": fps, "frame_count": frames, "sha256": sha256_file(path)}
    return details


def _relative(value: float | None) -> float | None:
    return None if value is None else round(float(value) - BASE_TIMESTAMP, 6)


def _stable_entity(entity: dict, source_keys: dict[int, str]) -> dict:
    return {
        "fused_entity_id": entity["fused_entity_id"],
        "entity_type": entity["entity_type"],
        "point_map": {"x": round(entity["point_map"]["x"], 6),
                      "y": round(entity["point_map"]["y"], 6)},
        "zone_id": entity["zone_id"],
        "confidence": None if entity["confidence"] is None else round(entity["confidence"], 6),
        "quality": entity["quality"],
        "members": [{
            "source_key": source_keys.get(int(member["source_id"]), str(member["source_id"])),
            "local_entity_id": member.get("local_entity_id"),
            "point_map": {"x": round(member["point_map"][0], 6),
                          "y": round(member["point_map"][1], 6)},
            "source_event_id": member.get("source_event_id"),
        } for member in entity.get("members", [])],
    }


def _cache_alert(alert: dict, video_time_s: float) -> dict:
    payload = alert.get("payload") or {}
    evidence = dict(payload.get("evidence_window") or {})
    for key in ("from", "to", "since", "until"):
        if evidence.get(key) is not None:
            evidence[key] = _relative(evidence[key])
    raw_result = payload.get("query_result") or {}
    result_rows = []
    for source_row in raw_result.get("rows", []):
        result_row = dict(source_row)
        if result_row.get("as_of") is not None:
            result_row["as_of"] = _relative(result_row["as_of"])
        result_rows.append(result_row)
    return {
        "video_time_s": video_time_s,
        "title": alert["title"],
        "message": alert["message"],
        "triggered_value": payload.get("value"),
        "quality": payload.get("quality"),
        "condition": payload.get("condition"),
        "evidence_window": evidence,
        "query_result": {
            "subject": payload.get("subject"), "measure": payload.get("measure"),
            "shape": raw_result.get("shape", "scalar"), "rows": result_rows,
            "evidence_window": evidence,
        },
    }


def carry_media(previous: Path, raw_path: Path) -> dict:
    """Reuse a previous cache's validated media block for the same raw fixture.

    The media hashes describe the four source recordings. They are pinned to the
    fixture through `raw_fixture_sha256`: if the fixture is byte-identical, the
    videos it was derived from are the same files, and re-opening them adds no
    information. This exists so a machine without the NVIDIA recordings can
    rebuild the derived cache after a derivation-code change without silently
    dropping media provenance. It refuses when the fixture differs.
    """
    cache = json.loads(previous.read_text(encoding="utf-8"))
    metadata = cache.get("metadata", {})
    if metadata.get("raw_fixture_sha256") != sha256_file(raw_path):
        raise ValueError(
            f"{previous} was derived from a different raw fixture; revalidate the videos")
    media = metadata.get("media") or {}
    if not media:
        raise ValueError(f"{previous} carries no validated media block")
    return media


def build_cache(raw_path: Path = DEFAULT_RAW, recipe_path: Path = DEFAULT_RECIPE,
                asset_root: Path | None = None, validate_media: bool = True,
                max_samples: int | None = None, media_from: Path | None = None) -> dict:
    metadata, records = load_raw(raw_path)
    recipe = json.loads(recipe_path.read_text(encoding="utf-8"))
    sample_hz = float(recipe["replay"]["sample_rate_hz"])
    stride = max(1, round(float(metadata["fps"]) / sample_hz))
    by_frame: dict[int, list[dict]] = {}
    for record in records:
        by_frame.setdefault(int(record["frame_index"]), []).append(record)
    frame_indices = [index for index in sorted(by_frame) if index % stride == 0]
    if max_samples is not None:
        frame_indices = frame_indices[:max_samples]
    if not frame_indices:
        raise ValueError("raw fixture contains no frames at the configured analytical rate")

    media = {}
    if validate_media:
        resolved_assets = asset_root or demo_runtime.resolve_asset_root()
        if resolved_assets is None:
            raise ValueError("NVIDIA demo assets are unavailable; pass --asset-root")
        media = validate_videos(Path(resolved_assets), metadata)
    elif media_from is not None:
        media = carry_media(media_from, raw_path)

    with tempfile.TemporaryDirectory(prefix="manysight-derived-cache-") as temp_dir:
        workspace = Path(temp_dir) / "manysight.db"
        db.init_db(str(workspace))
        # Derivation must not inherit live bookkeeping from an earlier build in
        # the same process (the tests build two caches back to back).
        realtime.coordinator.reset()
        with db.using_database(str(workspace), close_on_exit=True):
            _actions, setup = demo_runtime._setup_workspace(
                workspace, "fixture-builder", "http://fixture.invalid")
            source_ids = setup["source_ids"]
            source_keys = {value: key for key, value in source_ids.items()}
            next_fused = iter(f"F{index:016d}" for index in range(1, 100000))
            derivation_now = [BASE_TIMESTAMP]
            timeline = []
            with patch("server.services.multiview._new_fused_id", side_effect=lambda: next(next_fused)), \
                    patch("server.db.now", side_effect=lambda: derivation_now[0]):
                for timeline_index, frame_index in enumerate(frame_indices):
                    frames = sorted(by_frame[frame_index], key=lambda item: item["source_key"])
                    observations = []
                    video_time = round(frame_index / float(metadata["fps"]), 6)
                    sample_ts = BASE_TIMESTAMP + video_time
                    derivation_now[0] = sample_ts
                    for frame in frames:
                        sample = DetectionSampleIn(
                            schema_version=2,
                            source_id=source_ids[frame["source_key"]],
                            sample_id=frame["sample_id"],
                            timestamp=sample_ts,
                            frame_index=frame_index,
                            entity_type="person",
                            attributes={"producer_kind": "derived_fixture_build",
                                        "video_time_s": video_time},
                            detections=[{
                                "entity_id": detection["local_track_id"]
                                or f"untracked-{frame_index}-{index}",
                                "label": "person",
                                "confidence": detection["confidence"],
                                "bbox_px": detection["bbox_px"],
                                "point_px": detection["point_px"],
                                "identity_scope": "source",
                                "identity_model_version": "yolo11n-bytetrack-fixture-v1",
                            } for index, detection in enumerate(frame["detections"])],
                        )
                        batch, _digest = detection_sample_batch(sample)
                        observations.extend(batch.observations)
                    processed = _process_observations(ObservationBatch(observations=observations))
                    if processed[0]["rejected"] or processed[0]["completed_samples"] != len(frames):
                        raise RuntimeError(f"frame {frame_index}: ManySight did not complete all source samples")
                    # Ingestion publishes completed samples to the live
                    # coordinator instead of fusing inline. Derivation runs the
                    # same group tick the running platform schedules, just
                    # driven by this loop instead of by a 100 Hz clock.
                    realtime.coordinator.drain()
                    zone_names = {row["id"]: row["name"] for row in db.q("SELECT id,name FROM zones")}
                    with db.transaction():
                        fired = alert_engine.evaluate_ongoing(sample_ts, zone_names)
                    query_result = queries.execute_saved_query(setup["query_id"])
                    row = query_result["rows"][0]
                    evidence = query_result["metadata"]["evidence_window"]
                    fused = multiview_router.current_entities(
                        group_id=setup["group_id"], entity_type="person")
                    timeline.append({
                        "type": "derived_sample",
                        "index": timeline_index,
                        "video_time_s": video_time,
                        "source_frame_index": frame_index,
                        "source_samples": [{
                            "source_key": frame["source_key"],
                            "sample_id": frame["sample_id"],
                            "detection_count": len(frame["detections"]),
                        } for frame in frames],
                        "fused_entities": [_stable_entity(entity, source_keys)
                                           for entity in fused["entities"]],
                        "kpi": {
                            "name": recipe["query"]["name"],
                            "measure": "current_occupancy",
                            "value": row.get("current_occupancy"),
                            "quality": row.get("quality", "unknown"),
                            "as_of": _relative(row.get("as_of")),
                            "evidence": {
                                "from": _relative(evidence.get("from")),
                                "to": _relative(evidence.get("to")),
                                "source_count": evidence.get("source_count", 0),
                                "basis": evidence.get("basis"),
                            },
                        },
                        "alert_events": [_cache_alert(alert, video_time) for alert in fired],
                    })

            zone = db.q1("SELECT * FROM zones WHERE id=?", (setup["zone_id"],))
            provenance = db.q(
                "SELECT * FROM zone_geometry_provenance WHERE zone_id=? ORDER BY id",
                (setup["zone_id"],),
            )
            geometry = {
                "zone_name": recipe["zone"]["name"],
                "canonical_geometry": db.jload(zone["geometry_json"], {}),
                "canonical_revision": zone["revision"],
                "camera_contributions": [{
                    "source_key": source_keys[item["source_id"]],
                    "operation": item["operation"],
                    "source_calibration_revision": item["source_calibration_revision"],
                    "zone_view_revision": item["zone_view_revision"],
                    "image_polygon_px": db.jload(item["original_pixel_polygon_json"], []),
                    "projected_polygon_m": db.jload(item["projected_map_polygon_json"], []),
                } for item in provenance],
            }
    payload_hash = canonical_hash({"geometry": geometry, "timeline": timeline})
    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    cache = {
        "metadata": {
            "type": "manysight_derived_replay_cache",
            "fixture_version": 3,
            "schema_version": 1,
            "recipe_version": recipe["recipe_version"],
            "raw_fixture_sha256": sha256_file(raw_path),
            "recipe_sha256": sha256_file(recipe_path),
            "geometry_hash": canonical_hash(geometry),
            "fusion_config_hash": canonical_hash(recipe["multiview"]),
            "derivation_code_hash": derivation_hash(),
            "manysight_observation_schema_version": 2,
            "generated_at": generated_at,
            "sample_rate_hz": sample_hz,
            "source_fps": metadata["fps"],
            "duration_s": metadata["duration_s"],
            "sample_count": len(timeline),
            "payload_sha256": payload_hash,
            "media": media,
        },
        "geometry": geometry,
        "timeline": timeline,
    }
    qualifying = [entry for entry in timeline if entry["kpi"]["quality"] == "known"
                  and (entry["kpi"]["value"] or 0) >= recipe["alert"]["value"]]
    alerts = [event for entry in timeline for event in entry["alert_events"]]
    if max_samples is None and (not qualifying or not alerts):
        raise RuntimeError("derived replay does not reach the configured KPI/alert acceptance scenario")
    return cache


def write_cache(cache: dict, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(cache, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", type=Path, default=DEFAULT_RAW)
    parser.add_argument("--recipe", type=Path, default=DEFAULT_RECIPE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--asset-root", type=Path)
    parser.add_argument("--skip-video-validation", action="store_true")
    parser.add_argument("--media-from", type=Path,
                        help="reuse the media block of an existing cache built from the same "
                             "raw fixture (only with --skip-video-validation)")
    args = parser.parse_args()
    cache = build_cache(args.raw, args.recipe, args.asset_root,
                        validate_media=not args.skip_video_validation,
                        media_from=args.media_from)
    write_cache(cache, args.output)
    print(json.dumps({
        "output": str(args.output),
        "sample_count": cache["metadata"]["sample_count"],
        "sample_rate_hz": cache["metadata"]["sample_rate_hz"],
        "payload_sha256": cache["metadata"]["payload_sha256"],
    }, indent=2))


if __name__ == "__main__":
    main()
