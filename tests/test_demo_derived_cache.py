import hashlib
import json
from pathlib import Path

from demo.build_mv3dt_demo_fixture import DEFAULT_RAW, DEFAULT_RECIPE, build_cache
from server.services import demo_runtime


ROOT = Path(__file__).parents[1]
CACHE = ROOT / "demo" / "fixtures" / "nvidia_mv3dt_derived_replay.json"
CAMERAS = [f"Warehouse_Synthetic_Cam{i:03d}" for i in range(1, 5)]


def canonical_hash(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def test_text_provenance_hash_is_identical_for_lf_and_crlf(tmp_path):
    lf = tmp_path / "fixture-lf.jsonl"
    crlf = tmp_path / "fixture-crlf.jsonl"
    lf.write_bytes(b'{"frame":1}\n{"frame":2}\n')
    crlf.write_bytes(b'{"frame":1}\r\n{"frame":2}\r\n')

    assert demo_runtime._text_sha256(lf) == demo_runtime._text_sha256(crlf)


def test_cache_validation_never_reads_python_source_bytes(monkeypatch):
    original = Path.read_bytes

    def reject_python_source(path):
        if path.suffix == ".py":
            raise AssertionError(f"cache validation read Python source bytes: {path}")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", reject_python_source)
    demo_runtime.load_derived_cache.cache_clear()
    try:
        assert demo_runtime.load_derived_cache()["timeline"]
    finally:
        demo_runtime.load_derived_cache.cache_clear()


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
    assert "derivation_code_hash" not in first["metadata"]
    assert first["metadata"]["payload_sha256"] == second["metadata"]["payload_sha256"]
    assert first["timeline"] == second["timeline"]
    assert first["geometry"] == second["geometry"]
    assert first["timeline"][0]["source_frame_index"] == 0
    assert len(first["timeline"][0]["source_samples"]) == 4
    assert first["timeline"][0]["fused_entities"]
