"""Worker SDK contract helpers."""

import sys
from types import SimpleNamespace

from sdk.python.storelens import StoreLens


def test_detection_frame_count_has_no_analytics_window_metadata():
    client = StoreLens(batch_size=100)
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


def test_detection_frame_count_rejects_negative_values():
    client = StoreLens(batch_size=100)

    try:
        client.submit_detection_frame(source_id=7, entity_type="person", count=-1)
    except ValueError as exc:
        assert str(exc) == "detection frame count must be non-negative"
    else:
        raise AssertionError("negative frame counts must be rejected")


def test_open_capture_prefers_explicit_override(monkeypatch):
    opened = []
    monkeypatch.setitem(sys.modules, "cv2", SimpleNamespace(VideoCapture=lambda target: opened.append(target) or target))
    client = StoreLens(credential_access_key="resolve")
    monkeypatch.setattr(client, "get_source_connection", lambda _sid: (_ for _ in ()).throw(AssertionError("must not resolve")))
    client.open_capture({"id": 1, "kind": "rtsp", "connection_management": "storelens_managed"}, "override")
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
    client = StoreLens(credential_access_key="resolve")
    monkeypatch.setattr(client, "get_source_connection", lambda _sid: {
        "kind": "rtsp", "connection_management": "storelens_managed",
        "connection": {"host": "camera.local", "port": 8554, "path": "/live", "scheme": "rtsp", "transport": "tcp",
                       "username": "a@b", "password": "do not print"},
    })
    client.open_capture({"id": 5, "kind": "rtsp", "connection_management": "storelens_managed"})
    assert opened == ["rtsp://a%40b:do%20not%20print@camera.local:8554/live"]
    assert options_during_open == ["rtsp_transport;tcp"]
    assert __import__("os").environ.get("OPENCV_FFMPEG_CAPTURE_OPTIONS") is None
    assert "do not print" not in capsys.readouterr().out


def test_open_capture_resolves_external_reference(monkeypatch):
    opened = []
    monkeypatch.setitem(sys.modules, "cv2", SimpleNamespace(VideoCapture=lambda target: opened.append(target) or target))
    monkeypatch.setenv("LOCAL_CAMERA", "http://127.0.0.1/video")
    client = StoreLens()
    client.open_capture({
        "id": 3, "kind": "http", "connection_management": "external_secret",
        "locator": {"local_secret_ref": "LOCAL_CAMERA"},
    })
    assert opened == ["http://127.0.0.1/video"]


def test_get_source_connection_uses_dedicated_header(monkeypatch):
    client = StoreLens(api_key="normal", credential_access_key="privileged")
    seen = {}

    class Response:
        ok = True
        status_code = 200
        text = ""
        def json(self):
            return {"connection": {}}

    def fake_get(url, headers, timeout):
        seen.update(url=url, headers=headers, timeout=timeout)
        return Response()

    monkeypatch.setattr(client.session, "get", fake_get)
    client.get_source_connection(7)
    assert seen["headers"] == {"X-StoreLens-Credential-Key": "privileged"}


def test_open_capture_resolves_managed_webcam(monkeypatch):
    opened = []
    monkeypatch.setitem(sys.modules, "cv2", SimpleNamespace(VideoCapture=lambda target: opened.append(target) or target))
    client = StoreLens()
    monkeypatch.setattr(client, "get_source_connection", lambda _sid: (_ for _ in ()).throw(AssertionError("webcam needs no secret lookup")))
    client.open_capture({"id": 2, "kind": "webcam", "connection_management": "storelens_managed", "connection": {"device_index": 2}})
    assert opened == [2]


def test_open_capture_resolves_managed_http_basic(monkeypatch):
    opened = []
    monkeypatch.setitem(sys.modules, "cv2", SimpleNamespace(VideoCapture=lambda target: opened.append(target) or target))
    client = StoreLens(credential_access_key="resolve")
    monkeypatch.setattr(client, "get_source_connection", lambda _sid: {
        "kind": "http", "connection": {"url": "http://camera.local/video?x=1", "auth_type": "basic",
                                        "username": "user name", "password": "p@ss"},
    })
    client.open_capture({"id": 4, "kind": "http", "connection_management": "storelens_managed"})
    assert opened == ["http://user%20name:p%40ss@camera.local/video?x=1"]


def test_open_capture_resolves_managed_file(monkeypatch):
    opened = []
    monkeypatch.setitem(sys.modules, "cv2", SimpleNamespace(VideoCapture=lambda target: opened.append(target) or target))
    client = StoreLens(credential_access_key="resolve")
    monkeypatch.setattr(client, "get_source_connection", lambda _sid: {
        "kind": "file", "connection": {"path": r"C:\videos\demo.mp4"},
    })
    client.open_capture({"id": 8, "kind": "file", "connection_management": "storelens_managed"})
    assert opened == [r"C:\videos\demo.mp4"]


def test_open_capture_configuration_error_redacts_resolved_values(monkeypatch):
    monkeypatch.setitem(sys.modules, "cv2", SimpleNamespace(VideoCapture=lambda target: target))
    client = StoreLens(credential_access_key="resolve")
    monkeypatch.setattr(client, "get_source_connection", lambda _sid: {
        "kind": "rtsp", "connection": {"password": "must-not-leak"},
    })
    try:
        client.open_capture({"id": 6, "kind": "rtsp", "connection_management": "storelens_managed"})
    except RuntimeError as exc:
        assert "source 6 (rtsp)" in str(exc)
        assert "must-not-leak" not in str(exc)
    else:
        raise AssertionError("incomplete managed connection must fail")
