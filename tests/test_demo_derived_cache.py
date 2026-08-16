import hashlib
import json
from pathlib import Path

from demo.build_mv3dt_demo_fixture import DEFAULT_RAW, DEFAULT_RECIPE, build_cache


ROOT = Path(__file__).parents[1]
CACHE = ROOT / "demo" / "fixtures" / "nvidia_mv3dt_derived_replay.json"
CAMERAS = [f"Warehouse_Synthetic_Cam{i:03d}" for i in range(1, 5)]


def canonical_hash(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def test_committed_cache_provenance_timeline_geometry_and_acceptance():
    cache = json.loads(CACHE.read_text(encoding="utf-8"))
    metadata = cache["metadata"]
    assert metadata["raw_fixture_sha256"] == hashlib.sha256(DEFAULT_RAW.read_bytes()).hexdigest()
    assert metadata["recipe_sha256"] == hashlib.sha256(DEFAULT_RECIPE.read_bytes()).hexdigest()
    assert metadata["payload_sha256"] == canonical_hash({
        "geometry": cache["geometry"], "timeline": cache["timeline"],
    })
    assert metadata["sample_rate_hz"] == 10
    assert metadata["source_fps"] == 30
    assert metadata["sample_count"] == 201
    assert [item["source_frame_index"] for item in cache["timeline"]] == list(range(0, 601, 3))

    contributions = cache["geometry"]["camera_contributions"]
    assert [item["source_key"] for item in contributions] == CAMERAS[2:]
    assert contributions[0]["image_polygon_px"] == [
        {"x": 945, "y": 1080}, {"x": 1720, "y": 1080},
        {"x": 1235, "y": 0}, {"x": 960, "y": 0},
    ]
    assert contributions[1]["image_polygon_px"] == [
        {"x": 0, "y": 980}, {"x": 735, "y": 1080},
        {"x": 881, "y": 0}, {"x": 617, "y": 0},
    ]
    assert cache["geometry"]["canonical_geometry"]["type"] == "Polygon"
    assert cache["geometry"]["canonical_revision"] == 2

    qualifying = [item for item in cache["timeline"] if item["kpi"]["quality"] == "known"
                  and item["kpi"]["value"] >= 2]
    alerts = [event for item in cache["timeline"] for event in item["alert_events"]]
    assert qualifying and qualifying[0]["kpi"]["evidence"]["source_count"] == 4
    assert alerts


def test_builder_runs_raw_samples_through_real_manysight_pipeline_deterministically():
    first = build_cache(DEFAULT_RAW, DEFAULT_RECIPE, None, validate_media=False, max_samples=2)
    second = build_cache(DEFAULT_RAW, DEFAULT_RECIPE, None, validate_media=False, max_samples=2)
    assert first["metadata"]["payload_sha256"] == second["metadata"]["payload_sha256"]
    assert first["timeline"] == second["timeline"]
    assert first["geometry"] == second["geometry"]
    assert first["timeline"][0]["source_frame_index"] == 0
    assert len(first["timeline"][0]["source_samples"]) == 4
    assert first["timeline"][0]["fused_entities"]
