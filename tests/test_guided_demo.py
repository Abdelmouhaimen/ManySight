import json
import time
from pathlib import Path

import pytest

from demo.validate_mv3dt_fixture import validate


CAMERAS = [f"Warehouse_Synthetic_Cam{i:03d}" for i in range(1, 5)]


def _assets(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "assets"
    videos = root / "videos"
    videos.mkdir(parents=True)
    for camera in CAMERAS:
        (videos / f"{camera}.mp4").write_bytes(b"test-media")
    (root / "map.png").write_bytes(b"test-bird-view")
    monkeypatch.setenv("MANYSIGHT_DEMO_ASSET_DIR", str(root))
    return root


def test_repository_bundle_is_the_default_demo_runtime_media(client):
    from server.services import demo_runtime

    assert demo_runtime.resolve_asset_root() == demo_runtime.BUNDLED_ASSET_ROOT.resolve()
    assets = client.get("/api/v1/demo/assets")
    assert assets.status_code == 200
    assert assets.json()["available"] is True
    assert assets.json()["asset_source"] == "repository_bundle"


def test_repository_bundle_videos_match_the_committed_replay_provenance():
    from server.services import demo_runtime

    media = demo_runtime.load_derived_cache()["metadata"]["media"]
    root = demo_runtime.BUNDLED_ASSET_ROOT
    for camera in CAMERAS:
        digest = demo_runtime._sha256(root / "videos" / f"{camera}.mp4")
        assert digest == media[camera]["sha256"]


def test_canonical_fixture_is_synchronized_and_worker_raw_only():
    fixture = Path(__file__).parents[1] / "demo" / "fixtures" / "nvidia_mv3dt_yolo11n_bytetrack.jsonl"
    result = validate(fixture)
    assert result["frames"] == result["timestamps"] * 4
    assert result["timestamps"] == 602
    metadata = json.loads(fixture.read_text(encoding="utf-8").splitlines()[0])
    recipe = json.loads((fixture.parent / "nvidia_mv3dt_recipe.json").read_text(encoding="utf-8"))
    assert "mtmc_12cam" in metadata["dataset"]
    assert metadata["camera_keys"] == CAMERAS
    assert metadata["duration_s"] == pytest.approx(recipe["frame"]["duration_s"], abs=1e-5)
    assert metadata["processed_stride"] == 1
    assert metadata["producer"]["device"] == "CUDA"
    assert recipe["world_frame"]["name"] == "nvidia_mtmc_12cam_world"
    assert recipe["replay"]["sample_rate_hz"] == 10.0
    assert recipe["zone"]["name"] == "Aisle 04"
    assert recipe["zone"]["seed_camera_key"] == CAMERAS[2]
    assert [camera["key"] for camera in recipe["cameras"] if camera.get("zone_view_px")] == CAMERAS[2:]


def _apply_all_stages(client, session_id: str) -> dict:
    from server.services.demo_runtime import REQUEST_STAGE_ORDER

    latest = None
    for stage in REQUEST_STAGE_ORDER:
        response = client.post(
            f"/api/v1/demo/sessions/{session_id}/apply-request", json={"stage": stage})
        assert response.status_code == 200, response.text
        assert response.json()["applied"] is True
        latest = response.json()
    return latest


def test_guided_session_starts_with_camera_and_space_setup_only(client, tmp_path, monkeypatch):
    _assets(tmp_path, monkeypatch)
    session = client.post("/api/v1/demo/sessions", json={"mode": "guided"}).json()
    headers = {"X-ManySight-Demo-Session": session["id"]}
    # Prepared: the space, the four cameras, their calibrations, and the group.
    assert len(client.get("/api/v1/sources", headers=headers).json()) == 4
    assert len(client.get("/api/v1/calibrations", headers=headers).json()) == 4
    assert len(client.get("/api/v1/multiview/groups", headers=headers).json()) == 1
    assert session["result"]["group_id"]
    # Not yet created: the monitored zone and everything derived from it.
    assert client.get("/api/v1/zones", headers=headers).json() == []
    assert client.get("/api/v1/zone-views", headers=headers).json() == []
    assert client.get("/api/v1/queries", headers=headers).json() == []
    assert client.get("/api/v1/alert-rules", headers=headers).json() == []
    assert client.get("/api/v1/dashboards", headers=headers).json() == []
    assert "zone_id" not in session["result"]
    assert "query_id" not in session["result"]
    assert all(overlay["zones"] == [] for overlay in session["result"]["camera_overlays"].values()), \
        "no camera claims a zone trace before that zone view exists"

    # A learn-by-exploring session is configured up front, as before.
    learn = client.post("/api/v1/demo/sessions", json={"mode": "learn"}).json()
    learn_headers = {"X-ManySight-Demo-Session": learn["id"]}
    assert len(client.get("/api/v1/zones", headers=learn_headers).json()) == 1
    assert learn["result"]["query_id"] and learn["result"]["alert_rule_id"]
    assert learn["result"]["dashboard_id"]
    for camera in CAMERAS[2:]:
        assert learn["result"]["camera_overlays"][camera]["zones"][0]["name"] == "Aisle 04"
    client.post(f"/api/v1/demo/sessions/{learn['id']}/discard")
    client.post(f"/api/v1/demo/sessions/{session['id']}/discard")


def test_request_stages_are_ordered_and_idempotent(client, tmp_path, monkeypatch):
    _assets(tmp_path, monkeypatch)
    session = client.post("/api/v1/demo/sessions", json={"mode": "guided"}).json()
    headers = {"X-ManySight-Demo-Session": session["id"]}
    apply_url = f"/api/v1/demo/sessions/{session['id']}/apply-request"
    assert client.post(apply_url, json={"stage": "nonsense"}).status_code == 422
    assert client.post(apply_url, json={"stage": "query"}).status_code == 409, \
        "the zone must exist before the query that filters on it"

    first = client.post(apply_url, json={"stage": "zone_seed"})
    assert first.status_code == 200, first.text
    assert first.json()["applied"] is True
    zone_id = first.json()["result"]["zone_id"]
    assert len(client.get("/api/v1/zones", headers=headers).json()) == 1
    assert len(client.get("/api/v1/zone-views", headers=headers).json()) == 1
    overlays = client.get(f"/api/v1/demo/sessions/{session['id']}").json()["result"]["camera_overlays"]
    assert overlays[CAMERAS[2]]["zones"][0]["name"] == "Aisle 04"
    assert overlays[CAMERAS[3]]["zones"] == [], "camera 4 has not contributed yet"

    repeat = client.post(apply_url, json={"stage": "zone_seed"})
    assert repeat.status_code == 200
    assert repeat.json()["applied"] is False
    assert repeat.json()["result"]["zone_id"] == zone_id
    assert len(client.get("/api/v1/zones", headers=headers).json()) == 1
    assert len(client.get("/api/v1/zone-views", headers=headers).json()) == 1

    assert client.post(apply_url, json={"stage": "zone_extend"}).json()["applied"] is True
    assert client.post(apply_url, json={"stage": "zone_extend"}).json()["applied"] is False
    assert len(client.get("/api/v1/zone-views", headers=headers).json()) == 2
    assert client.post(apply_url, json={"stage": "alert"}).status_code == 409
    assert client.post(apply_url, json={"stage": "query"}).json()["applied"] is True
    assert client.post(apply_url, json={"stage": "alert"}).json()["applied"] is True
    assert client.post(apply_url, json={"stage": "dashboard"}).json()["applied"] is True
    assert len(client.get("/api/v1/alert-rules", headers=headers).json()) == 1
    assert len(client.get("/api/v1/dashboards", headers=headers).json()) == 1
    client.post(f"/api/v1/demo/sessions/{session['id']}/discard")


def test_demo_workspace_is_isolated_and_cached_replay_is_truthful(client, tmp_path, monkeypatch):
    _assets(tmp_path, monkeypatch)
    assert client.get("/api/v1/sources").json() == []
    created = client.post("/api/v1/demo/sessions", json={"mode": "guided"})
    assert created.status_code == 201, created.text
    session = created.json()
    headers = {"X-ManySight-Demo-Session": session["id"]}
    assert len(client.get("/api/v1/sources", headers=headers).json()) == 4
    assert client.get("/api/v1/sources").json() == []
    assert client.get("/api/v1/jobs", headers=headers).json() == []
    assert all(item["status"] == "completed" for item in session["action_log"])
    _apply_all_stages(client, session["id"])
    session = client.get(f"/api/v1/demo/sessions/{session['id']}").json()
    assert all(item["status"] == "completed" for item in session["action_log"])
    assert session["result"]["query_id"]
    overlays = session["result"]["camera_overlays"]
    assert set(overlays) == set(CAMERAS)
    for camera in CAMERAS:
        assert overlays[camera]["source_id"] == session["result"]["source_ids"][camera]
        assert overlays[camera]["frame_width"] == 1920
        assert overlays[camera]["frame_height"] == 1080
        assert overlays[camera]["camera_key"] == camera
        assert overlays[camera]["fps"] == 30.0
    assert overlays[CAMERAS[0]]["zones"] == []
    assert overlays[CAMERAS[1]]["zones"] == []
    for camera in CAMERAS[2:]:
        assert overlays[camera]["zones"][0]["name"] == "Aisle 04"
        assert len(overlays[camera]["zones"][0]["polygons_px"][0]) == 4
    demo_zones = client.get("/api/v1/zones", headers=headers).json()
    assert len(demo_zones) == 1
    assert demo_zones[0]["name"] == "Aisle 04"
    assert demo_zones[0]["geometry"]["type"] == "Polygon"
    assert demo_zones[0]["component_count"] == 1
    assert len(client.get("/api/v1/zone-views", headers=headers).json()) == 2
    provenance = client.get(f"/api/v1/zones/{demo_zones[0]['id']}", headers=headers).json()["geometry_provenance"]
    assert [item["operation"] for item in provenance] == [
        "create_from_camera_polygon", "extend_from_zone_view",
    ]

    started = client.post(f"/api/v1/demo/sessions/{session['id']}/start")
    assert started.status_code == 200, started.text
    assert started.json()["master_clock"]["status"] == "running"
    cache = client.get(f"/api/v1/demo/sessions/{session['id']}/replay-cache").json()
    assert cache["metadata"]["type"] == "manysight_derived_replay_cache"
    assert cache["metadata"]["source_fps"] == 30
    assert cache["metadata"]["sample_rate_hz"] == 10
    assert cache["metadata"]["sample_count"] == 201
    assert cache["timeline"][0]["source_frame_index"] == 0
    assert cache["timeline"][-1]["source_frame_index"] == 600
    evidence = client.get("/api/v1/observations?limit=500", headers=headers).json()
    assert evidence["total"] == 0  # playable demo performs no ongoing central processing
    assert client.get("/api/v1/observations?limit=1").json()["total"] == 0
    discarded = client.post(f"/api/v1/demo/sessions/{session['id']}/discard")
    assert discarded.status_code == 200


def test_promotion_copies_setup_only_by_default(client, tmp_path, monkeypatch):
    _assets(tmp_path, monkeypatch)
    from server.services import demo_media
    monkeypatch.setattr(demo_media, "start", lambda _root: "http://127.0.0.1:8765")
    created = client.post("/api/v1/demo/sessions", json={"mode": "learn"}).json()
    promoted = client.post(
        f"/api/v1/demo/sessions/{created['id']}/promote",
        json={"include_recorded_observations": False},
    )
    assert promoted.status_code == 200, promoted.text
    assert len(client.get("/api/v1/sources").json()) == 4
    assert len(client.get("/api/v1/calibrations").json()) == 4
    assert len(client.get("/api/v1/multiview/groups").json()) == 1
    assert client.get("/api/v1/zones").json() == []
    assert client.get("/api/v1/queries").json() == []
    assert client.get("/api/v1/dashboards").json() == []
    assert client.get("/api/v1/alert-rules").json() == []
    assert client.get("/api/v1/alerts").json() == []
    assert client.get("/api/v1/observations").json()["total"] == 0


def test_failed_promotion_rolls_back_and_remains_retryable(client, tmp_path, monkeypatch, isolated_db):
    _assets(tmp_path, monkeypatch)
    from server.services import demo_media
    monkeypatch.setattr(demo_media, "start", lambda _root: (_ for _ in ()).throw(RuntimeError("test failure")))
    created = client.post("/api/v1/demo/sessions", json={}).json()
    with pytest.raises(RuntimeError, match="test failure"):
        client.post(f"/api/v1/demo/sessions/{created['id']}/promote", json={})
    assert client.get("/api/v1/sources").json() == []
    row = isolated_db.q1("SELECT status,workspace_path FROM demo_sessions WHERE id=?", (created["id"],))
    assert row["status"] == "paused"
    assert Path(row["workspace_path"]).is_file()
    client.post(f"/api/v1/demo/sessions/{created['id']}/discard")


def test_replay_master_clock_pause_and_resume(client, tmp_path, monkeypatch):
    _assets(tmp_path, monkeypatch)
    session = client.post("/api/v1/demo/sessions", json={}).json()
    client.post(f"/api/v1/demo/sessions/{session['id']}/start")
    try:
        time.sleep(0.25)
        current = client.get(f"/api/v1/demo/sessions/{session['id']}").json()
        assert .15 <= current["master_clock"]["position_s"] <= .8
        paused = client.post(f"/api/v1/demo/sessions/{session['id']}/pause").json()
        paused_position = paused["master_clock"]["position_s"]
        time.sleep(.2)
        still_paused = client.get(f"/api/v1/demo/sessions/{session['id']}").json()
        assert still_paused["master_clock"]["position_s"] == pytest.approx(paused_position, abs=.01)
        client.post(f"/api/v1/demo/sessions/{session['id']}/start")
        time.sleep(0.1)
        resumed = client.get(f"/api/v1/demo/sessions/{session['id']}").json()
        assert resumed["master_clock"]["position_s"] > paused_position
    finally:
        client.post(f"/api/v1/demo/sessions/{session['id']}/discard")


def test_short_replay_clock_loops_as_one_epoch(client, tmp_path, monkeypatch):
    _assets(tmp_path, monkeypatch)
    from server.services import demo_runtime
    session = client.post("/api/v1/demo/sessions", json={}).json()
    from server import db
    from server.services import demo_runtime
    demo_runtime._normal_ex("UPDATE demo_sessions SET duration_s=.2 WHERE id=?", (session["id"],))
    client.post(f"/api/v1/demo/sessions/{session['id']}/start")
    try:
        time.sleep(.46)
        current = client.get(f"/api/v1/demo/sessions/{session['id']}").json()
        assert current["master_clock"]["epoch"] >= 2
        assert 0 <= current["master_clock"]["position_s"] < .2
        assert current["master_clock"]["absolute_s"] >= .4
    finally:
        client.post(f"/api/v1/demo/sessions/{session['id']}/discard")


def test_demo_media_is_allowlisted(client, tmp_path, monkeypatch):
    _assets(tmp_path, monkeypatch)
    session = client.post("/api/v1/demo/sessions", json={}).json()
    good = client.get(f"/api/v1/demo/media/{CAMERAS[0]}.mp4?demo_session={session['id']}")
    assert good.status_code == 200
    plan = client.get(f"/api/v1/demo/plan.png?demo_session={session['id']}")
    assert plan.status_code == 200
    assert plan.headers["content-type"] == "image/png"
    assert plan.content == b"test-bird-view"
    evidence = client.get(
        f"/api/v1/demo/sessions/{session['id']}/camera-evidence/{CAMERAS[0]}"
    )
    assert evidence.status_code == 200
    payload = evidence.json()
    assert payload["camera_key"] == CAMERAS[0]
    assert payload["fps"] == 30.0
    assert payload["frame_count"] == 602
    assert len(payload["frames"]) == 602
    assert set(payload["frames"][0]["detections"][0]) == {
        "local_track_id", "confidence", "bbox_px", "point_px",
    }
    bad = client.get(f"/api/v1/demo/media/not-a-camera.mp4?demo_session={session['id']}")
    assert bad.status_code == 404
    client.post(f"/api/v1/demo/sessions/{session['id']}/discard")


def test_obsolete_dataset_sessions_are_not_reused(client, tmp_path, monkeypatch, isolated_db):
    _assets(tmp_path, monkeypatch)
    session = client.post("/api/v1/demo/sessions", json={}).json()
    isolated_db.ex(
        "UPDATE demo_sessions SET recipe_version='obsolete-mtmc4-recipe' WHERE id=?",
        (session["id"],),
    )
    assert client.get("/api/v1/demo/sessions/active").json() is None
    assert client.get(f"/api/v1/demo/sessions/{session['id']}").status_code == 409
    assert client.get(
        f"/api/v1/demo/media/{CAMERAS[0]}.mp4?demo_session={session['id']}"
    ).status_code == 409
    client.post(f"/api/v1/demo/sessions/{session['id']}/discard")


def test_obsolete_derived_cache_sessions_are_not_reused(client, tmp_path, monkeypatch, isolated_db):
    _assets(tmp_path, monkeypatch)
    session = client.post("/api/v1/demo/sessions", json={}).json()
    result = session["result"]
    result["derived_replay"]["payload_sha256"] = "obsolete-cache"
    isolated_db.ex(
        "UPDATE demo_sessions SET result_json=? WHERE id=?",
        (json.dumps(result), session["id"]),
    )
    assert client.get("/api/v1/demo/sessions/active").json() is None
    assert client.get(f"/api/v1/demo/sessions/{session['id']}").status_code == 409
    assert client.get(
        f"/api/v1/demo/media/{CAMERAS[0]}.mp4?demo_session={session['id']}"
    ).status_code == 409
    assert client.get(
        f"/api/v1/demo/sessions/{session['id']}/camera-evidence/{CAMERAS[0]}"
    ).status_code == 409
    client.post(f"/api/v1/demo/sessions/{session['id']}/discard")


def test_learn_calibration_uses_real_homography_then_restores_validated_matrix(
        client, tmp_path, monkeypatch):
    _assets(tmp_path, monkeypatch)
    session = client.post("/api/v1/demo/sessions", json={"mode": "learn"}).json()
    headers = {"X-ManySight-Demo-Session": session["id"]}
    source_id = next(iter(session["result"]["source_ids"].values()))
    pixels = [{"x": 300, "y": 300}, {"x": 1200, "y": 250},
              {"x": 1500, "y": 850}, {"x": 500, "y": 900}]
    projected = client.post(
        f"/api/v1/sources/{source_id}/project", headers=headers, json={"points": pixels},
    ).json()["points"]
    pairs = [{"px": pixel, "map": mapped} for pixel, mapped in zip(pixels, projected)]
    practice = client.put(
        f"/api/v1/sources/{source_id}/calibration", headers=headers,
        json={"points": pairs, "frame_w": 1920, "frame_h": 1080},
    )
    assert practice.status_code == 200
    restored = client.post(
        f"/api/v1/demo/sessions/{session['id']}/restore-practice-calibration",
        json={"source_id": source_id},
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["comparison"]["max_difference_m"] < 0.01
    assert restored.json()["used_for_replay"] == "validated_nvidia_calibration"
    source = client.get(f"/api/v1/sources/{source_id}", headers=headers).json()
    assert source["calibration"]["provider"] == "nvidia_mv3dt"
    client.post(f"/api/v1/demo/sessions/{session['id']}/discard")


def test_practice_plan_trace_is_restored_to_the_prepared_demo_space(client, tmp_path, monkeypatch):
    _assets(tmp_path, monkeypatch)
    session = client.post("/api/v1/demo/sessions", json={"mode": "guided"}).json()
    headers = {"X-ManySight-Demo-Session": session["id"]}
    prepared_store = client.get("/api/v1/store", headers=headers).json()
    prepared_sources = client.get("/api/v1/sources", headers=headers).json()
    assert all(source["calibrated"] and source["placement"] for source in prepared_sources)

    traced = client.post("/api/v1/store/blueprint", headers=headers, json={
        "image_width": 800, "image_height": 600,
        "polygons_px": [[{"x": 0, "y": 0}, {"x": 400, "y": 0},
                         {"x": 400, "y": 300}, {"x": 0, "y": 300}]],
        "scale_points_px": [{"x": 0, "y": 0}, {"x": 100, "y": 0}],
        "known_distance_m": 5, "origin_px": {"x": 0, "y": 0}, "y_axis_up": True,
    })
    assert traced.status_code == 200, traced.text
    assert traced.json()["invalidated_calibrations"] == 4
    practising = client.get("/api/v1/sources", headers=headers).json()
    assert not any(source["calibrated"] or source["placement"] for source in practising)

    restored = client.post(f"/api/v1/demo/sessions/{session['id']}/restore-practice-space")
    assert restored.status_code == 200, restored.text
    payload = restored.json()
    assert payload["comparison"]["practice_trace_present"] is True
    assert payload["comparison"]["practice_width_m"] == pytest.approx(20.0)
    assert [item["camera_key"] for item in payload["restored_sources"]] == CAMERAS
    assert all(item["calibration_restored"] for item in payload["restored_sources"])

    store = client.get("/api/v1/store", headers=headers).json()
    assert store["width_m"] == pytest.approx(prepared_store["width_m"])
    assert store["height_m"] == pytest.approx(prepared_store["height_m"])
    assert store["map"]["floor_polygons"] == prepared_store["map"]["floor_polygons"]
    sources = client.get("/api/v1/sources", headers=headers).json()
    assert all(source["calibrated"] and source["placement"] for source in sources)
    for before, after in zip(prepared_sources, sources):
        assert after["calibration"]["provider"] == "nvidia_mv3dt"
        assert after["calibration"]["H"] == before["calibration"]["H"]
        assert after["placement"]["x"] == pytest.approx(before["placement"]["x"])
    assert client.get(f"/api/v1/demo/sessions/{session['id']}/replay-cache").status_code == 200
    # The walkthrough continues from restored geometry: the canonical zone still
    # projects into the prepared metric frame.
    _apply_all_stages(client, session["id"])
    zone = client.get("/api/v1/zones", headers=headers).json()[0]
    assert zone["name"] == "Aisle 04"
    assert zone["geometry"]["type"] == "Polygon"
    xs = [point[0] for ring in zone["geometry"]["coordinates"] for point in ring]
    assert max(xs) <= prepared_store["width_m"] + 1e-6
    client.post(f"/api/v1/demo/sessions/{session['id']}/discard")


def test_practice_space_restore_requires_an_active_session(client, tmp_path, monkeypatch):
    _assets(tmp_path, monkeypatch)
    session = client.post("/api/v1/demo/sessions", json={}).json()
    client.post(f"/api/v1/demo/sessions/{session['id']}/discard")
    assert client.post(
        f"/api/v1/demo/sessions/{session['id']}/restore-practice-space"
    ).status_code == 409
    assert client.post("/api/v1/demo/sessions/unknown/restore-practice-space").status_code == 404


def test_opt_in_observation_promotion_remaps_sources_and_drops_demo_zone_links(
        client, tmp_path, monkeypatch):
    _assets(tmp_path, monkeypatch)
    from server.services import demo_media
    monkeypatch.setattr(demo_media, "start", lambda _root: "http://127.0.0.1:8765")
    existing = client.post("/api/v1/sources", json={"name": "Existing", "kind": "webcam"}).json()
    session = client.post("/api/v1/demo/sessions", json={"mode": "learn"}).json()
    client.post(f"/api/v1/demo/sessions/{session['id']}/start")
    time.sleep(0.4)
    promoted = client.post(
        f"/api/v1/demo/sessions/{session['id']}/promote",
        json={"include_recorded_observations": True},
    )
    assert promoted.status_code == 200, promoted.text
    assert promoted.json()["observations_promoted"] > 0
    observations = client.get("/api/v1/observations?limit=500").json()["observations"]
    assert observations
    assert all(item["source_id"] != existing["id"] for item in observations)
    assert all(item["zone_id"] is None for item in observations)
    assert all(item["attributes"]["promoted_from_demo"] == session["id"] for item in observations)


def test_committed_cache_contains_real_fused_query_and_alert_evidence(client, tmp_path, monkeypatch):
    _assets(tmp_path, monkeypatch)
    session = client.post("/api/v1/demo/sessions", json={}).json()
    cache = client.get(f"/api/v1/demo/sessions/{session['id']}/replay-cache").json()
    qualifying = [sample for sample in cache["timeline"] if sample["kpi"]["quality"] == "known"
                  and sample["kpi"]["value"] >= 2]
    alerts = [event for sample in cache["timeline"] for event in sample["alert_events"]]
    assert qualifying
    assert qualifying[0]["kpi"]["evidence"]["basis"] == "current complete samples"
    assert qualifying[0]["kpi"]["evidence"]["source_count"] == 4
    assert alerts
    client.post(f"/api/v1/demo/sessions/{session['id']}/discard")
