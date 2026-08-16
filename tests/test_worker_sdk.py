"""Worker SDK contract helpers."""

import sys
from types import SimpleNamespace

from sdk.python.manysight import ManySight


def test_detection_frame_count_has_no_analytics_window_metadata():
    client = ManySight(batch_size=100)
    client.submit_detection_frame(
        source_id=7,
        entity_type="person",
        count=0,
        ts=1234.25,
        observation_id="frame-count-1",
    )

    observation = client._obs_buffer.pop()
    assert observation["kind"] == "measurement"
    assert observation["name"] == "detection_frame_count"
    assert observation["label"] == "person"
    assert observation["value"] == 0
    assert observation["timestamp"] == 1234.25
    assert "attributes" not in observation
    assert "window_s" not in observation


def test_preferred_sdk_posts_one_atomic_detection_sample(monkeypatch):
    client = ManySight(batch_size=100)
    seen = {}

    def request(method, path, body=None, params=None):
        seen.update(method=method, path=path, body=body, params=params)
        return {"sample_status": "completed"}

    monkeypatch.setattr(client, "_req", request)
    result = client.submit_detection_sample(
        source_id=7,
        entity_type="person",
        sample_id="cam7-frame42",
        timestamp=1234.5,
        frame_index=42,
        detections=[{"entity_id": "t1", "bbox_px": (1, 2, 3, 4), "confidence": .8}],
    )
    assert result["sample_status"] == "completed"
    assert seen["path"] == "/detection-samples"
    assert seen["body"]["frame_index"] == 42
    assert seen["body"]["detections"][0]["bbox_px"] == (1, 2, 3, 4)
    assert client._obs_buffer == []


def test_empty_sdk_builder_posts_known_zero(monkeypatch):
    client = ManySight(batch_size=100)
    seen = {}
    monkeypatch.setattr(client, "_req", lambda method, path, body=None, params=None:
                        seen.setdefault("body", body) or {"sample_status": "completed"})
    builder = client.begin_detection_sample(
        7, "person", ts=10.0, sample_id="empty", frame_index=5)
    builder.submit()
    assert seen["body"]["detections"] == []
    assert seen["body"]["sample_id"] == "empty"


def test_detection_frame_count_rejects_negative_values():
    client = ManySight(batch_size=100)

    try:
        client.submit_detection_frame(source_id=7, entity_type="person", count=-1)
    except ValueError as exc:
        assert str(exc) == "detection frame count must be non-negative"
    else:
        raise AssertionError("negative frame counts must be rejected")


def test_three_track_frame_uses_one_exact_timestamp_and_marker_last():
    client = ManySight(batch_size=100)
    sample_ts = 1786480000.125
    for entity_id in ("A", "B", "C"):
        client.submit_detection(
            source_id=7,
            entity_id=entity_id,
            entity_type="person",
            point_px=(100, 200),
            ts=sample_ts,
            observation_id=f"detection-{entity_id}",
        )
    client.submit_detection_frame(
        source_id=7,
        entity_type="person",
        count=3,
        ts=sample_ts,
        observation_id="frame-count-3",
    )

    assert [row["timestamp"] for row in client._obs_buffer] == [sample_ts] * 4
    assert [row["observation_id"] for row in client._obs_buffer] == [
        "detection-A", "detection-B", "detection-C", "frame-count-3",
    ]
    marker = client._obs_buffer[-1]
    assert {key: marker[key] for key in (
        "schema_version", "observation_id", "kind", "timestamp", "source_id",
        "name", "value", "value_kind", "unit", "label",
    )} == {
        "schema_version": 2,
        "observation_id": "frame-count-3",
        "kind": "measurement",
        "timestamp": sample_ts,
        "source_id": 7,
        "name": "detection_frame_count",
        "value": 3,
        "value_kind": "gauge",
        "unit": "tracks",
        "label": "person",
    }
    client._obs_buffer.clear()


def test_explicit_zero_timestamp_is_preserved_for_empty_frame():
    client = ManySight(batch_size=100)
    client.submit_detection_frame(
        source_id=7,
        entity_type="person",
        count=0,
        ts=0.0,
        observation_id="empty-at-epoch",
    )

    assert client._obs_buffer[0]["timestamp"] == 0.0
    assert client._obs_buffer[0]["value"] == 0
    client._obs_buffer.clear()


def test_frame_count_rejects_fractional_or_boolean_values():
    client = ManySight(batch_size=100)
    for invalid in (1.5, True):
        try:
            client.submit_detection_frame(source_id=7, entity_type="person", count=invalid)
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid frame count {invalid!r} must be rejected")


def test_open_capture_prefers_explicit_override(monkeypatch):
    opened = []
    monkeypatch.setitem(sys.modules, "cv2", SimpleNamespace(VideoCapture=lambda target: opened.append(target) or target))
    client = ManySight()
    monkeypatch.setattr(client, "get_source_connection", lambda _sid: (_ for _ in ()).throw(AssertionError("must not resolve")))
    client.open_capture({"id": 1, "kind": "rtsp", "connection_management": "manysight_managed"}, "override")
    assert opened == ["override"]


def test_open_capture_resolves_managed_rtsp_without_logging_secret(monkeypatch, capsys):
    opened = []
    options_during_open = []
    monkeypatch.delenv("OPENCV_FFMPEG_CAPTURE_OPTIONS", raising=False)
    def open_capture(target):
        opened.append(target)
        options_during_open.append(__import__("os").environ.get("OPENCV_FFMPEG_CAPTURE_OPTIONS"))
        return target
    monkeypatch.setitem(sys.modules, "cv2", SimpleNamespace(VideoCapture=open_capture))
    client = ManySight()
    monkeypatch.setattr(client, "get_source_connection", lambda _sid: {
        "kind": "rtsp", "connection_management": "manysight_managed",
        "connection": {"host": "camera.local", "port": 8554, "path": "/live", "scheme": "rtsp", "transport": "tcp",
                       "username": "a@b", "password": "do not print"},
    })
    client.open_capture({"id": 5, "kind": "rtsp", "connection_management": "manysight_managed"})
    assert opened == ["rtsp://a%40b:do%20not%20print@camera.local:8554/live"]
    assert options_during_open == ["rtsp_transport;tcp"]
    assert __import__("os").environ.get("OPENCV_FFMPEG_CAPTURE_OPTIONS") is None
    assert "do not print" not in capsys.readouterr().out


def test_open_capture_resolves_external_reference(monkeypatch):
    opened = []
    monkeypatch.setitem(sys.modules, "cv2", SimpleNamespace(VideoCapture=lambda target: opened.append(target) or target))
    monkeypatch.setenv("LOCAL_CAMERA", "http://127.0.0.1/video")
    client = ManySight()
    client.open_capture({
        "id": 3, "kind": "http", "connection_management": "external_secret",
        "locator": {"local_secret_ref": "LOCAL_CAMERA"},
    })
    assert opened == ["http://127.0.0.1/video"]


def test_get_source_connection_needs_no_key_beyond_the_api_key(monkeypatch):
    """Resolution rides the client's ordinary session; there is no second key."""
    client = ManySight(api_key="normal")
    seen = {}

    class Response:
        ok = True
        status_code = 200
        text = ""
        def json(self):
            return {"connection": {}}

    def fake_get(url, timeout):
        seen.update(url=url, timeout=timeout)
        return Response()

    monkeypatch.setattr(client.session, "get", fake_get)
    client.get_source_connection(7)
    assert seen["url"].endswith("/sources/7/connection")
    assert client.session.headers["X-API-Key"] == "normal"
    assert not [name for name in vars(client) if "access_key" in name], \
        "no dedicated resolution key survives on the client"


def test_a_client_with_no_api_key_still_resolves(monkeypatch):
    """The local-only default: no keys configured anywhere, and it works."""
    client = ManySight()
    monkeypatch.setattr(client.session, "get", lambda url, timeout: type(
        "R", (), {"ok": True, "status_code": 200, "json": staticmethod(
            lambda: {"connection": {"url": "http://cam/s.mjpg"}})})())
    assert client.get_source_connection(3)["connection"]["url"] == "http://cam/s.mjpg"


def test_open_capture_resolves_managed_webcam(monkeypatch):
    opened = []
    monkeypatch.setitem(sys.modules, "cv2", SimpleNamespace(VideoCapture=lambda target: opened.append(target) or target))
    client = ManySight()
    monkeypatch.setattr(client, "get_source_connection", lambda _sid: (_ for _ in ()).throw(AssertionError("webcam needs no secret lookup")))
    client.open_capture({"id": 2, "kind": "webcam", "connection_management": "manysight_managed", "connection": {"device_index": 2}})
    assert opened == [2]


def test_open_capture_resolves_managed_http_basic(monkeypatch):
    opened = []
    monkeypatch.setitem(sys.modules, "cv2", SimpleNamespace(VideoCapture=lambda target: opened.append(target) or target))
    client = ManySight()
    monkeypatch.setattr(client, "get_source_connection", lambda _sid: {
        "kind": "http", "connection": {"url": "http://camera.local/video?x=1", "auth_type": "basic",
                                        "username": "user name", "password": "p@ss"},
    })
    client.open_capture({"id": 4, "kind": "http", "connection_management": "manysight_managed"})
    assert opened == ["http://user%20name:p%40ss@camera.local/video?x=1"]


def test_open_capture_resolves_managed_file(monkeypatch):
    opened = []
    monkeypatch.setitem(sys.modules, "cv2", SimpleNamespace(VideoCapture=lambda target: opened.append(target) or target))
    client = ManySight()
    monkeypatch.setattr(client, "get_source_connection", lambda _sid: {
        "kind": "file", "connection": {"path": r"C:\videos\demo.mp4"},
    })
    client.open_capture({"id": 8, "kind": "file", "connection_management": "manysight_managed"})
    assert opened == [r"C:\videos\demo.mp4"]


def test_open_capture_configuration_error_redacts_resolved_values(monkeypatch):
    monkeypatch.setitem(sys.modules, "cv2", SimpleNamespace(VideoCapture=lambda target: target))
    client = ManySight()
    monkeypatch.setattr(client, "get_source_connection", lambda _sid: {
        "kind": "rtsp", "connection": {"password": "must-not-leak"},
    })
    try:
        client.open_capture({"id": 6, "kind": "rtsp", "connection_management": "manysight_managed"})
    except RuntimeError as exc:
        assert "source 6 (rtsp)" in str(exc)
        assert "must-not-leak" not in str(exc)
    else:
        raise AssertionError("incomplete managed connection must fail")
