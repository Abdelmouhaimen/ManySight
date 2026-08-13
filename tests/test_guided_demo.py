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
    monkeypatch.setenv("STORELENS_DEMO_ASSET_DIR", str(root))
    return root


def test_canonical_fixture_is_synchronized_and_worker_raw_only():
    result = validate(Path(__file__).parents[1] / "demo" / "fixtures" /
                      "nvidia_mv3dt_yolo11n_bytetrack.jsonl")
    assert result["frames"] == result["timestamps"] * 4
    assert result["timestamps"] >= 60


def test_demo_workspace_is_isolated_and_replay_is_truthful(client, tmp_path, monkeypatch):
    _assets(tmp_path, monkeypatch)
    assert client.get("/api/v1/sources").json() == []
    created = client.post("/api/v1/demo/sessions", json={"mode": "guided"})
    assert created.status_code == 201, created.text
    session = created.json()
    headers = {"X-StoreLens-Demo-Session": session["id"]}
    assert len(client.get("/api/v1/sources", headers=headers).json()) == 4
    assert client.get("/api/v1/sources").json() == []
    assert client.get("/api/v1/jobs", headers=headers).json() == []
    assert all(item["status"] == "completed" for item in session["action_log"])
    assert session["result"]["query_id"]

    started = client.post(f"/api/v1/demo/sessions/{session['id']}/start")
    assert started.status_code == 200, started.text
    time.sleep(1.2)
    evidence = client.get("/api/v1/observations?limit=500", headers=headers).json()
    assert evidence["total"] > 0
    assert {item["attributes"].get("producer_kind") for item in evidence["observations"]} == {"replay"}
    assert client.get("/api/v1/observations?limit=1").json()["total"] == 0
    discarded = client.post(f"/api/v1/demo/sessions/{session['id']}/discard")
    assert discarded.status_code == 200


def test_promotion_copies_setup_only_by_default(client, tmp_path, monkeypatch):
    _assets(tmp_path, monkeypatch)
    from server.services import demo_media
    monkeypatch.setattr(demo_media, "start", lambda _root: "http://127.0.0.1:8765")
    created = client.post("/api/v1/demo/sessions", json={}).json()
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


def test_replay_pause_and_future_frame_order(client, tmp_path, monkeypatch):
    _assets(tmp_path, monkeypatch)
    session = client.post("/api/v1/demo/sessions", json={}).json()
    headers = {"X-StoreLens-Demo-Session": session["id"]}
    client.post(f"/api/v1/demo/sessions/{session['id']}/start")
    try:
        time.sleep(0.25)
        first = client.get("/api/v1/observations?limit=500", headers=headers).json()
        assert first["total"] > 0
        current = client.get(f"/api/v1/demo/sessions/{session['id']}").json()
        latest_fixture_time = max(
            item["attributes"]["source_frame_index"] / 30 for item in first["observations"])
        assert latest_fixture_time <= current["playback_position_s"] + 0.15
        client.post(f"/api/v1/demo/sessions/{session['id']}/pause")
        paused_total = client.get("/api/v1/observations?limit=1", headers=headers).json()["total"]
        time.sleep(1.05)
        assert client.get("/api/v1/observations?limit=1", headers=headers).json()["total"] == paused_total
        client.post(f"/api/v1/demo/sessions/{session['id']}/start")
        time.sleep(0.25)
    finally:
        client.post(f"/api/v1/demo/sessions/{session['id']}/discard")


def test_short_replay_loops_namespace_identity_and_prune_history(client, tmp_path, monkeypatch):
    _assets(tmp_path, monkeypatch)
    from server.services import demo_runtime
    metadata = {"duration_s": 0.2}
    frames = []
    for stamp, frame_index in ((0.0, 0), (0.1, 1)):
        for camera in CAMERAS:
            frames.append({
                "source_key": camera, "video_time_s": stamp, "frame_index": frame_index,
                "detection_frame_count": 1,
                "detections": [{"local_track_id": "7", "confidence": 0.9,
                                "bbox_px": [100, 100, 150, 250], "point_px": [125, 250]}],
            })
    monkeypatch.setattr(demo_runtime, "load_fixture", lambda: (metadata, frames))
    session = client.post("/api/v1/demo/sessions", json={}).json()
    headers = {"X-StoreLens-Demo-Session": session["id"]}
    client.post(f"/api/v1/demo/sessions/{session['id']}/start")
    try:
        deadline = time.time() + 5
        current = None
        while time.time() < deadline:
            current = client.get(f"/api/v1/demo/sessions/{session['id']}").json()
            if current["playback_epoch"] >= 2:
                break
            time.sleep(0.1)
        assert current["playback_epoch"] >= 2
        rows = client.get("/api/v1/observations?limit=500", headers=headers).json()["observations"]
        detection_rows = [item for item in rows if item["kind"] == "detection"]
        epochs = {int(item["entity_id"].split(":", 1)[0][1:]) for item in detection_rows}
        assert len(epochs) <= current["resource_usage"]["retained_epochs"]
        assert max(epochs) >= 1
        epoch_times = {}
        for item in detection_rows:
            epoch = int(item["entity_id"].split(":", 1)[0][1:])
            epoch_times.setdefault(epoch, []).append(item["ts"])
        ordered = sorted((epoch, min(values)) for epoch, values in epoch_times.items())
        assert [stamp for _, stamp in ordered] == sorted(stamp for _, stamp in ordered)
    finally:
        client.post(f"/api/v1/demo/sessions/{session['id']}/discard")


def test_demo_media_is_allowlisted(client, tmp_path, monkeypatch):
    _assets(tmp_path, monkeypatch)
    session = client.post("/api/v1/demo/sessions", json={}).json()
    good = client.get(f"/api/v1/demo/media/{CAMERAS[0]}.mp4?demo_session={session['id']}")
    assert good.status_code == 200
    bad = client.get(f"/api/v1/demo/media/not-a-camera.mp4?demo_session={session['id']}")
    assert bad.status_code == 404
    client.post(f"/api/v1/demo/sessions/{session['id']}/discard")


def test_learn_calibration_uses_real_homography_then_restores_validated_matrix(
        client, tmp_path, monkeypatch):
    _assets(tmp_path, monkeypatch)
    session = client.post("/api/v1/demo/sessions", json={"mode": "learn"}).json()
    headers = {"X-StoreLens-Demo-Session": session["id"]}
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


def test_opt_in_observation_promotion_remaps_sources_and_drops_demo_zone_links(
        client, tmp_path, monkeypatch):
    _assets(tmp_path, monkeypatch)
    from server.services import demo_media
    monkeypatch.setattr(demo_media, "start", lambda _root: "http://127.0.0.1:8765")
    existing = client.post("/api/v1/sources", json={"name": "Existing", "kind": "webcam"}).json()
    session = client.post("/api/v1/demo/sessions", json={}).json()
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


def test_four_camera_replay_reaches_real_fused_query_and_alert(client, tmp_path, monkeypatch):
    _assets(tmp_path, monkeypatch)
    session = client.post("/api/v1/demo/sessions", json={}).json()
    headers = {"X-StoreLens-Demo-Session": session["id"]}
    client.post(f"/api/v1/demo/sessions/{session['id']}/start")
    deadline = time.time() + 15
    result = None
    while time.time() < deadline:
        result = client.post(
            f"/api/v1/queries/{session['result']['query_id']}/execute", headers=headers,
        ).json()
        row = result["rows"][0]
        if row.get("quality") == "known" and (row.get("current_occupancy") or 0) >= 2:
            break
        time.sleep(0.25)
    assert result["rows"][0]["current_occupancy"] >= 2
    assert result["metadata"]["evidence_window"]["basis"] == "current complete samples"
    fused = client.get("/api/v1/multiview/current?entity_type=person", headers=headers).json()
    raw = client.get("/api/v1/observations/latest-frames?entity_type=person", headers=headers).json()
    raw_count = sum(len(frame["detections"]) for frame in raw["frames"])
    assert raw_count >= len(fused["entities"])
    assert client.get("/api/v1/alerts", headers=headers).json()
    client.post(f"/api/v1/demo/sessions/{session['id']}/discard")
