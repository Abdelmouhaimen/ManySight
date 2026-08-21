"""Managed source connection persistence, encryption, and authorization."""
import base64
import sqlite3

import pytest

from server import db
from server.services import credentials as credential_store

KEY = base64.urlsafe_b64encode(b"k" * 32).decode()


def managed_rtsp(password="camera-pass"):
    return {
        "name": "Entrance",
        "kind": "rtsp",
        "connection_management": "manysight_managed",
        "connection": {"host": "10.0.0.8", "port": 554, "path": "/live", "transport": "tcp"},
        "credentials": {"username": "operator", "password": password},
    }


def test_managed_credentials_are_encrypted_and_hidden(client, isolated_db, monkeypatch):
    monkeypatch.setenv("MANYSIGHT_CREDENTIAL_KEY", KEY)
    response = client.post("/api/v1/sources", json=managed_rtsp())
    assert response.status_code == 201, response.text
    source = response.json()
    serialized = str(source)
    assert "camera-pass" not in serialized and "operator" not in serialized
    assert source["connection"]["host"] == "10.0.0.8"
    assert source["credential_status"] == {"configured": True, "username_configured": True}

    stored = isolated_db.q1("SELECT encrypted_payload FROM source_credentials WHERE source_id=?", (source["id"],))
    # The envelope version travels with the associated data it is bound to.
    assert stored["encrypted_payload"].startswith(credential_store.PREFIX)
    assert "camera-pass" not in stored["encrypted_payload"]
    assert "operator" not in stored["encrypted_payload"]
    assert "camera-pass".encode() not in open(isolated_db.DB_PATH, "rb").read()
    assert "camera-pass" not in str(client.get("/api/v1/sources").json())
    assert "operator" not in str(client.get(f"/api/v1/sources/{source['id']}").json())


def test_managed_credentials_resolve_without_a_second_access_key(client, monkeypatch):
    """A local deployment needs no key beyond the one guarding the API itself.

    The property that still matters is *which operation* returns secrets, not
    how many keys guard it: resolution hands back the stored username and
    password, and nothing else does. `test_api_auth` covers the case where an
    API key is configured, including that public reads never reach this route.
    """
    monkeypatch.setenv("MANYSIGHT_CREDENTIAL_KEY", KEY)
    source = client.post("/api/v1/sources", json=managed_rtsp()).json()
    resolved = client.get(f"/api/v1/sources/{source['id']}/connection")
    assert resolved.status_code == 200
    assert resolved.json()["connection"]["username"] == "operator"
    assert resolved.json()["connection"]["password"] == "camera-pass"
    assert resolved.json()["connection_management"] == "manysight_managed"


def test_managed_auth_type_none_resolves_the_url_directly(client):
    """No stored secret, so nothing to decrypt — and no encryption key needed."""
    source = client.post("/api/v1/sources", json={
        "name": "Open camera", "kind": "http", "connection_management": "manysight_managed",
        "connection": {"url": "http://cam.internal/stream.mjpg", "auth_type": "none"},
    }).json()
    assert source["credential_status"]["configured"] is False
    resolved = client.get(f"/api/v1/sources/{source['id']}/connection")
    assert resolved.status_code == 200
    assert resolved.json()["connection"] == {
        "url": "http://cam.internal/stream.mjpg", "auth_type": "none"}


def test_missing_encryption_key_fails_closed_without_partial_source(client, isolated_db, monkeypatch):
    monkeypatch.delenv("MANYSIGHT_CREDENTIAL_KEY", raising=False)
    response = client.post("/api/v1/sources", json=managed_rtsp())
    assert response.status_code == 503
    assert isolated_db.q("SELECT * FROM sources") == []


def test_edit_preserves_replaces_and_clears_credentials(client, monkeypatch):
    monkeypatch.setenv("MANYSIGHT_CREDENTIAL_KEY", KEY)
    source = client.post("/api/v1/sources", json=managed_rtsp()).json()
    sid = source["id"]

    assert client.put(f"/api/v1/sources/{sid}", json={"name": "Renamed"}).status_code == 200
    assert client.get(f"/api/v1/sources/{sid}/connection").json()["connection"]["password"] == "camera-pass"
    assert client.put(f"/api/v1/sources/{sid}", json={"credentials": {"username": "new", "password": "new-pass"}}).status_code == 200
    assert client.get(f"/api/v1/sources/{sid}/connection").json()["connection"]["password"] == "new-pass"
    cleared = client.put(f"/api/v1/sources/{sid}", json={"clear_credentials": True})
    assert cleared.json()["credential_status"] == {"configured": False, "username_configured": False}
    assert "password" not in client.get(f"/api/v1/sources/{sid}/connection").json()["connection"]


def test_invalid_edit_does_not_destroy_existing_credentials(client, monkeypatch):
    monkeypatch.setenv("MANYSIGHT_CREDENTIAL_KEY", KEY)
    source = client.post("/api/v1/sources", json=managed_rtsp()).json()
    sid = source["id"]
    bad = client.put(f"/api/v1/sources/{sid}", json={"connection": {"host": "rtsp://bad"}, "clear_credentials": True})
    assert bad.status_code == 422
    resolved = client.get(f"/api/v1/sources/{sid}/connection").json()
    assert resolved["connection"]["password"] == "camera-pass"


def test_external_secret_mode_remains_supported(client):
    source = client.post("/api/v1/sources", json={
        "name": "External", "kind": "http", "connection_management": "external_secret",
        "locator": {"local_secret_ref": "CAMERA_URL"},
    }).json()
    resolved = client.get(f"/api/v1/sources/{source['id']}/connection").json()
    assert resolved["connection"] == {"local_secret_ref": "CAMERA_URL"}


def test_source_delete_removes_ciphertext(client, isolated_db, monkeypatch):
    monkeypatch.setenv("MANYSIGHT_CREDENTIAL_KEY", KEY)
    source = client.post("/api/v1/sources", json=managed_rtsp()).json()
    assert client.delete(f"/api/v1/sources/{source['id']}").status_code == 200
    assert isolated_db.q("SELECT * FROM source_credentials") == []


def test_source_delete_removes_current_state_but_retains_history(client, calibrated_source):
    sample = {
        "source_id": calibrated_source,
        "entity_type": "person",
        "timestamp": 1000,
        "sample_id": "before-delete",
        "detections": [{
            "schema_version": 2,
            "observation_id": "before-delete-person",
            "kind": "detection",
            "timestamp": 1000,
            "source_id": calibrated_source,
            "entity_type": "person",
            "entity_id": "person-1",
            "geometry": {"point_px": [100, 200]},
        }],
    }
    response = client.post("/api/v1/detection-samples", json=sample)
    assert response.status_code == 200, response.text
    assert db.q("SELECT * FROM source_current_samples WHERE source_id=?", (calibrated_source,))

    assert client.delete(f"/api/v1/sources/{calibrated_source}").status_code == 200
    assert db.q("SELECT * FROM source_current_samples WHERE source_id=?", (calibrated_source,)) == []
    assert db.q("SELECT * FROM source_current_entities WHERE source_id=?", (calibrated_source,)) == []
    assert db.q("SELECT * FROM events WHERE source_id=?", (calibrated_source,))
    latest = client.get("/api/v1/observations/latest-frames", params={"entity_type": "person"})
    assert latest.status_code == 200
    assert latest.json()["frames"] == []


def test_wrong_encryption_key_cannot_decrypt_existing_credentials(client, monkeypatch):
    monkeypatch.setenv("MANYSIGHT_CREDENTIAL_KEY", KEY)
    source = client.post("/api/v1/sources", json=managed_rtsp()).json()
    monkeypatch.setenv("MANYSIGHT_CREDENTIAL_KEY", base64.urlsafe_b64encode(b"x" * 32).decode())
    response = client.get(f"/api/v1/sources/{source['id']}/connection")
    assert response.status_code == 500
    assert "camera-pass" not in response.text


@pytest.mark.parametrize(("kind", "connection"), [
    ("webcam", {"device_index": 0}),
    ("http", {"url": "http://127.0.0.1:8765/stream.mjpg", "auth_type": "none"}),
    ("file", {"path": r"C:\demo\video.mp4"}),
])
def test_managed_noncredential_source_kinds_persist(client, kind, connection):
    response = client.post("/api/v1/sources", json={
        "name": kind, "kind": kind, "connection_management": "manysight_managed",
        "connection": connection,
    })
    assert response.status_code == 201, response.text
    assert response.json()["connection"] == connection
    assert response.json()["credential_status"]["configured"] is False


@pytest.mark.parametrize("body", [
    {"name": "bad webcam", "kind": "webcam", "connection_management": "manysight_managed", "connection": {"device_index": -1}},
    {"name": "bad rtsp", "kind": "rtsp", "connection_management": "manysight_managed", "connection": {"host": "rtsp://bad", "port": 554, "path": "/live"}},
    {"name": "embedded", "kind": "http", "connection_management": "manysight_managed", "connection": {"url": "http://user:pass@camera/video"}},
    {"name": "token URL", "kind": "http", "connection_management": "manysight_managed", "connection": {"url": "http://camera/video?token=secret"}},
    {"name": "wrong fields", "kind": "file", "connection_management": "manysight_managed", "connection": {"url": "http://camera/video"}},
])
def test_malformed_managed_connections_are_rejected(client, body):
    assert client.post("/api/v1/sources", json=body).status_code == 422


def test_host_path_update_preserves_credentials(client, monkeypatch):
    monkeypatch.setenv("MANYSIGHT_CREDENTIAL_KEY", KEY)
    source = client.post("/api/v1/sources", json=managed_rtsp()).json()
    updated = client.put(f"/api/v1/sources/{source['id']}", json={
        "connection": {"host": "10.0.0.9", "port": 8554, "path": "/new", "transport": "udp"},
    })
    assert updated.status_code == 200
    resolved = client.get(f"/api/v1/sources/{source['id']}/connection").json()
    assert resolved["connection"]["host"] == "10.0.0.9"
    assert resolved["connection"]["password"] == "camera-pass"
