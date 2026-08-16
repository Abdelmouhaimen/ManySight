"""The static bundle the public landing page is built from.

These run against a miniature demo — two cameras, three derived samples — so the
exporter's behaviour can be checked without the multi-hundred-megabyte dataset.
The validation path is the real one: the fixture files are hashed and the cache
metadata is built with the platform's own helpers, so a test cache is accepted
or rejected for exactly the same reasons a real one would be.
"""
import json

import pytest

from demo import export_static_landing_demo as exporter
from server.services import demo_runtime


RECIPE = {
    "schema_version": 1,
    "recipe_version": "test-recipe-v1",
    "frame": {"width": 1920, "height": 1080, "fps": 30.0, "duration_s": 2.0},
    "replay": {"sample_rate_hz": 10.0},
    "store": {"name": "ManySight test warehouse", "width_m": 20.0, "height_m": 10.0},
    "zone": {"name": "Aisle 04", "ztype": "aisle", "color": "#ef4444"},
    "cameras": [
        {"key": "Cam001", "name": "Test camera 1", "zone_view_px": None},
        {"key": "Cam002", "name": "Test camera 2",
         "zone_view_px": [[10, 20], [30, 20], [30, 40], [10, 40]]},
    ],
    "multiview": {"time_tolerance_s": 0.2, "spatial_gate_m": 1.5},
    "query": {"name": "People in Aisle 04"},
    "dashboard": {"name": "Aisle monitoring"},
    "alert": {"name": "At least two people", "operator": ">=", "value": 2, "cooldown_s": 30},
}

FIXTURE_METADATA = {
    "type": "fixture_metadata",
    "schema_version": 2,
    "fixture_version": 3,
    "dataset": "test dataset",
    "fps": 30,
    "frame_count": 60,
    "duration_s": 2.0,
    "producer": {"detector": "yolo11n", "tracker": "ByteTrack"},
    "camera_keys": ["Cam001", "Cam002"],
}

FIXTURE_RECORDS = [
    {"type": "detection_sample", "source_key": "Cam001", "frame_index": 0, "video_time_s": 0.0,
     "sample_id": "Cam001-frame-0",
     "detections": [{"bbox_px": [1, 2, 3, 4], "point_px": [2, 4],
                     "local_track_id": "1", "confidence": 0.9}]},
    {"type": "detection_sample", "source_key": "Cam002", "frame_index": 0, "video_time_s": 0.0,
     "sample_id": "Cam002-frame-0", "detections": []},
    # Deliberately out of order, to prove the exporter sorts by frame.
    {"type": "detection_sample", "source_key": "Cam001", "frame_index": 2, "video_time_s": 0.066,
     "sample_id": "Cam001-frame-2", "detections": []},
    {"type": "detection_sample", "source_key": "Cam001", "frame_index": 1, "video_time_s": 0.033,
     "sample_id": "Cam001-frame-1",
     "detections": [{"bbox_px": [5, 6, 7, 8], "point_px": [6, 8],
                     "local_track_id": "1", "confidence": 0.8}]},
]

GEOMETRY = {
    "zone_name": "Aisle 04",
    "canonical_revision": 1,
    "canonical_geometry": {
        "type": "Polygon",
        "coordinates": [[[2.0, 2.0], [6.0, 2.0], [6.0, 8.0], [2.0, 8.0], [2.0, 2.0]]],
    },
    "camera_contributions": [],
}

TIMELINE = [
    {"type": "derived_sample", "index": 0, "video_time_s": 0.0, "source_frame_index": 0,
     "source_samples": [],
     "kpi": {"name": "People in Aisle 04", "measure": "current_occupancy", "value": 1,
             "quality": "known", "as_of": 0.0, "evidence": {"source_count": 2}},
     "fused_entities": [{"fused_entity_id": 7, "point_map": {"x": 3.0, "y": 3.0},
                         "members": [{"source_key": "Cam001", "local_entity_id": "1"}]}],
     "alert_events": []},
    {"type": "derived_sample", "index": 1, "video_time_s": 0.1, "source_frame_index": 3,
     "source_samples": [],
     "kpi": {"name": "People in Aisle 04", "measure": "current_occupancy", "value": 2,
             "quality": "known", "as_of": 0.1, "evidence": {"source_count": 2}},
     "fused_entities": [{"fused_entity_id": 7, "point_map": {"x": 4.0, "y": 3.0},
                         "members": [{"source_key": "Cam001", "local_entity_id": "1"}]}],
     "alert_events": [{"name": "At least two people", "video_time_s": 0.1}]},
    {"type": "derived_sample", "index": 2, "video_time_s": 0.2, "source_frame_index": 6,
     "source_samples": [],
     "kpi": {"name": "People in Aisle 04", "measure": "current_occupancy", "value": 0,
             "quality": "unknown", "as_of": 0.2, "evidence": {"source_count": 0}},
     "fused_entities": [], "alert_events": []},
]


@pytest.fixture
def demo_bundle(tmp_path, monkeypatch):
    """Point the platform's demo loaders at a miniature, internally valid demo."""
    recipe_path = tmp_path / "recipe.json"
    fixture_path = tmp_path / "fixture.jsonl"
    cache_path = tmp_path / "cache.json"

    recipe_path.write_text(json.dumps(RECIPE), encoding="utf-8")
    fixture_path.write_text(
        "\n".join(json.dumps(row) for row in [FIXTURE_METADATA, *FIXTURE_RECORDS]),
        encoding="utf-8",
    )

    monkeypatch.setattr(demo_runtime, "RECIPE", recipe_path)
    monkeypatch.setattr(demo_runtime, "FIXTURE", fixture_path)
    monkeypatch.setattr(demo_runtime, "DERIVED_CACHE", cache_path)
    demo_runtime.load_derived_cache.cache_clear()

    # Build metadata the same way the platform verifies it.
    metadata = {
        "type": "manysight_derived_replay_cache",
        "schema_version": 1,
        "fixture_version": 3,
        "generated_at": "2026-01-01T00:00:00+00:00",
        "duration_s": 2.0,
        "sample_count": len(TIMELINE),
        "recipe_version": RECIPE["recipe_version"],
        "raw_fixture_sha256": demo_runtime._sha256(fixture_path),
        "recipe_sha256": demo_runtime._sha256(recipe_path),
        "geometry_hash": demo_runtime._canonical_hash(GEOMETRY),
        "fusion_config_hash": demo_runtime._canonical_hash(RECIPE["multiview"]),
        "derivation_code_hash": demo_runtime.derivation_hash(),
        "sample_rate_hz": 10.0,
        "source_fps": 30,
        "payload_sha256": demo_runtime._canonical_hash(
            {"geometry": GEOMETRY, "timeline": TIMELINE}),
    }
    cache_path.write_text(
        json.dumps({"geometry": GEOMETRY, "metadata": metadata, "timeline": TIMELINE}),
        encoding="utf-8",
    )
    yield {"recipe": recipe_path, "fixture": fixture_path, "cache": cache_path}
    demo_runtime.load_derived_cache.cache_clear()


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


# ------------------------------------------------------------------ the happy path

def test_exports_a_manifest_and_a_replay(demo_bundle, tmp_path):
    output = tmp_path / "bundle"
    result = exporter.export(output, asset_root=None, skip_media=True)

    assert result["samples"] == 3
    assert (output / "manifest.json").is_file()
    assert (output / "replay.json").is_file()


def test_the_manifest_describes_every_camera_and_its_video(demo_bundle, tmp_path):
    output = tmp_path / "bundle"
    exporter.export(output, asset_root=None, skip_media=True)
    manifest = read(output / "manifest.json")

    assert [camera["name"] for camera in manifest["cameras"]] == ["Camera 1", "Camera 2"]
    assert [camera["video"] for camera in manifest["cameras"]] == [
        "./camera-1.mp4", "./camera-2.mp4"]
    assert manifest["replay"] == "./replay.json"
    assert manifest["plan"]["image"] == "./plan.png"
    assert manifest["duration_s"] == 2.0
    assert manifest["fps"] == 30


def test_only_cameras_that_see_the_zone_carry_its_outline(demo_bundle, tmp_path):
    output = tmp_path / "bundle"
    exporter.export(output, asset_root=None, skip_media=True)
    cameras = {camera["id"]: camera for camera in read(output / "manifest.json")["cameras"]}

    assert cameras["Cam001"]["zones"] == []
    assert cameras["Cam002"]["zones"][0]["name"] == "Aisle 04"
    assert cameras["Cam002"]["zones"][0]["polygons_px"] == [[[10, 20], [30, 20], [30, 40], [10, 40]]]


def test_the_canonical_zone_and_floor_size_reach_the_bundle(demo_bundle, tmp_path):
    output = tmp_path / "bundle"
    exporter.export(output, asset_root=None, skip_media=True)
    manifest = read(output / "manifest.json")

    assert manifest["zone"]["name"] == "Aisle 04"
    assert manifest["zone"]["polygon_m"][0] == [2.0, 2.0]
    assert manifest["plan"]["width_m"] == 20.0
    assert manifest["plan"]["height_m"] == 10.0


# ---------------------------------------------------------------- alert wording

def test_the_alert_threshold_and_its_exact_wording_are_exported(demo_bundle, tmp_path):
    """The page must not be able to word `>=` as "more than"."""
    output = tmp_path / "bundle"
    exporter.export(output, asset_root=None, skip_media=True)
    rule = read(output / "manifest.json")["alert_rule"]

    assert rule["operator"] == ">="
    assert rule["value"] == 2
    assert rule["phrase"] == "At least 2 people in Aisle 04"


def test_each_operator_gets_its_own_words():
    assert exporter.phrase_for(">", 2, "Aisle 04") == "More than 2 people in Aisle 04"
    assert exporter.phrase_for(">=", 2, "Aisle 04") == "At least 2 people in Aisle 04"
    assert exporter.phrase_for("<", 5, "Lobby") == "Fewer than 5 people in Lobby"
    with pytest.raises(exporter.ExportError):
        exporter.phrase_for("~=", 2, "Aisle 04")


# ------------------------------------------------------------- derived results

def test_the_result_and_its_quality_are_copied_not_recomputed(demo_bundle, tmp_path):
    output = tmp_path / "bundle"
    exporter.export(output, asset_root=None, skip_media=True)
    timeline = read(output / "replay.json")["timeline"]

    assert [sample["kpi"]["value"] for sample in timeline] == [1, 2, 0]
    assert [sample["kpi"]["quality"] for sample in timeline] == ["known", "known", "unknown"]
    # The trailing sample is a real unknown carrying a zero; both survive, so the
    # page can refuse to show the zero without the exporter deciding for it.
    assert timeline[2]["kpi"] == {"value": 0, "quality": "unknown", "source_count": 0}


def test_recorded_alerts_survive_with_their_timing(demo_bundle, tmp_path):
    output = tmp_path / "bundle"
    exporter.export(output, asset_root=None, skip_media=True)
    timeline = read(output / "replay.json")["timeline"]

    assert timeline[0]["alerts"] == []
    assert timeline[1]["alerts"] == [{"name": "At least two people", "video_time_s": 0.1}]
    assert timeline[2]["alerts"] == []


def test_combined_people_keep_their_identity_and_camera_membership(demo_bundle, tmp_path):
    output = tmp_path / "bundle"
    exporter.export(output, asset_root=None, skip_media=True)
    entities = read(output / "replay.json")["timeline"][0]["entities"]

    assert entities == [{
        "id": 7,
        "point_map": {"x": 3.0, "y": 3.0},
        "members": [{"camera": "Cam001", "local_track_id": "1"}],
    }]


def test_camera_frames_are_exported_per_camera_and_in_frame_order(demo_bundle, tmp_path):
    output = tmp_path / "bundle"
    exporter.export(output, asset_root=None, skip_media=True)
    frames = read(output / "replay.json")["camera_frames"]

    assert set(frames) == {"Cam001", "Cam002"}
    assert [frame["frame_index"] for frame in frames["Cam001"]] == [0, 1, 2]
    assert frames["Cam001"][0]["detections"][0]["bbox_px"] == [1, 2, 3, 4]
    assert frames["Cam001"][0]["detections"][0]["local_track_id"] == "1"
    # An empty frame is a real observation and must stay empty, not disappear.
    assert frames["Cam002"][0]["detections"] == []


# --------------------------------------------------------------- determinism

def test_two_exports_of_the_same_demo_are_byte_identical(demo_bundle, tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    exporter.export(first, asset_root=None, skip_media=True)
    exporter.export(second, asset_root=None, skip_media=True)

    for name in ("manifest.json", "replay.json"):
        assert (first / name).read_bytes() == (second / name).read_bytes()


# ------------------------------------------------------------------- refusals

def test_a_stale_cache_is_refused_rather_than_published(demo_bundle, tmp_path):
    """The whole point: ManySight will not publish what it considers invalid."""
    cache = read(demo_bundle["cache"])
    cache["metadata"]["derivation_code_hash"] = "0" * 64
    demo_bundle["cache"].write_text(json.dumps(cache), encoding="utf-8")
    demo_runtime.load_derived_cache.cache_clear()

    with pytest.raises(exporter.ExportError) as error:
        exporter.export(tmp_path / "bundle", asset_root=None, skip_media=True)
    assert "derivation_code_hash" in str(error.value)
    assert not (tmp_path / "bundle").exists(), "nothing may be written from an invalid cache"


def test_an_edited_recipe_is_refused_too(demo_bundle, tmp_path):
    recipe = read(demo_bundle["recipe"])
    recipe["store"]["name"] = "renamed after the cache was built"
    demo_bundle["recipe"].write_text(json.dumps(recipe), encoding="utf-8")
    demo_runtime.load_derived_cache.cache_clear()

    with pytest.raises(exporter.ExportError) as error:
        exporter.export(tmp_path / "bundle", asset_root=None, skip_media=True)
    assert "recipe_sha256" in str(error.value)


def test_missing_media_fails_loudly_instead_of_writing_half_a_bundle(demo_bundle, tmp_path):
    with pytest.raises(exporter.ExportError) as error:
        exporter.export(tmp_path / "bundle", asset_root=tmp_path / "nowhere", skip_media=False)
    assert "missing demo video" in str(error.value)


def test_the_cli_reports_a_stale_cache_and_exits_non_zero(demo_bundle, tmp_path, capsys):
    cache = read(demo_bundle["cache"])
    cache["metadata"]["payload_sha256"] = "0" * 64
    demo_bundle["cache"].write_text(json.dumps(cache), encoding="utf-8")
    demo_runtime.load_derived_cache.cache_clear()

    code = exporter.main(["--output", str(tmp_path / "bundle"), "--skip-media"])
    assert code == 1
    assert "payload_sha256" in capsys.readouterr().err


# --------------------------------------------------------------------- secrets

def test_nothing_private_reaches_the_public_bundle(demo_bundle, tmp_path):
    output = tmp_path / "bundle"
    exporter.export(output, asset_root=None, skip_media=True)

    for name in ("manifest.json", "replay.json"):
        payload = read(output / name)
        assert exporter.audit(payload) == []
        raw = (output / name).read_text(encoding="utf-8")
        assert str(tmp_path) not in raw, "a filesystem path leaked into the bundle"
        for word in ("password", "secret", "api_key", "rtsp://", "workspace_path"):
            assert word not in raw.lower()


def test_the_audit_actually_catches_things():
    assert exporter.audit({"password": "hunter2"})
    assert exporter.audit({"nested": [{"api_key": "x"}]})
    assert exporter.audit({"where": "/home/someone/workspace/demo.db"})
    assert exporter.audit({"feed": "rtsp://camera.local/stream"})
    assert exporter.audit({"value": 2, "quality": "known"}) == []


def test_the_demo_workspace_name_is_not_published(demo_bundle, tmp_path):
    """The bundle needs a floor size, not the workspace's name."""
    output = tmp_path / "bundle"
    exporter.export(output, asset_root=None, skip_media=True)
    raw = (output / "manifest.json").read_text(encoding="utf-8")
    assert "warehouse" not in raw.lower()


# ------------------------------------------------------------------ integrity

def test_a_camera_without_exported_frames_is_caught(demo_bundle, tmp_path):
    manifest = {"cameras": [{"id": "Cam001", "video": "./camera-1.mp4"}]}
    replay = {"camera_frames": {}, "timeline": [{"index": 0}]}
    with pytest.raises(exporter.ExportError, match="no exported frames"):
        exporter.check_bundle(manifest, replay)


def test_an_empty_timeline_is_caught(demo_bundle, tmp_path):
    manifest = {"cameras": [{"id": "Cam001", "video": "./camera-1.mp4"}]}
    replay = {"camera_frames": {"Cam001": []}, "timeline": []}
    with pytest.raises(exporter.ExportError, match="no derived samples"):
        exporter.check_bundle(manifest, replay)
