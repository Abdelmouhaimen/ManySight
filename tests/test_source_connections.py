"""Managed source connection persistence, encryption, and authorization."""
import base64
import sqlite3

import pytest

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


def test_privileged_resolution_is_header_only_and_ignores_public_reads(client, monkeypatch):
    monkeypatch.setenv("MANYSIGHT_CREDENTIAL_KEY", KEY)
    monkeypatch.setenv("MANYSIGHT_CREDENTIAL_ACCESS_KEY", "resolve-only")
    source = client.post("/api/v1/sources", json=managed_rtsp()).json()
    path = f"/api/v1/sources/{source['id']}/connection"
    assert client.get(path).status_code == 401
    assert client.get(path + "?api_key=resolve-only").status_code == 401
    assert client.get(path, headers={"X-ManySight-Credential-Key": "wrong"}).status_code == 401
    resolved = client.get(path, headers={"X-ManySight-Credential-Key": "resolve-only"})
    assert resolved.status_code == 200
    assert resolved.json()["connection"]["username"] == "operator"
    assert resolved.json()["connection"]["password"] == "camera-pass"


def test_missing_encryption_key_fails_closed_without_partial_source(client, isolated_db, monkeypatch):
    monkeypatch.delenv("MANYSIGHT_CREDENTIAL_KEY", raising=False)
    response = client.post("/api/v1/sources", json=managed_rtsp())
    assert response.status_code == 503
    assert isolated_db.q("SELECT * FROM sources") == []


def test_edit_preserves_replaces_and_clears_credentials(client, monkeypatch):
    monkeypatch.setenv("MANYSIGHT_CREDENTIAL_KEY", KEY)
    monkeypatch.setenv("MANYSIGHT_CREDENTIAL_ACCESS_KEY", "resolve")
    source = client.post("/api/v1/sources", json=managed_rtsp()).json()
    sid = source["id"]
    headers = {"X-ManySight-Credential-Key": "resolve"}

    assert client.put(f"/api/v1/sources/{sid}", json={"name": "Renamed"}).status_code == 200
    assert client.get(f"/api/v1/sources/{sid}/connection", headers=headers).json()["connection"]["password"] == "camera-pass"
    assert client.put(f"/api/v1/sources/{sid}", json={"credentials": {"username": "new", "password": "new-pass"}}).status_code == 200
    assert client.get(f"/api/v1/sources/{sid}/connection", headers=headers).json()["connection"]["password"] == "new-pass"
    cleared = client.put(f"/api/v1/sources/{sid}", json={"clear_credentials": True})
    assert cleared.json()["credential_status"] == {"configured": False, "username_configured": False}
    assert "password" not in client.get(f"/api/v1/sources/{sid}/connection", headers=headers).json()["connection"]


def test_invalid_edit_does_not_destroy_existing_credentials(client, monkeypatch):
    monkeypatch.setenv("MANYSIGHT_CREDENTIAL_KEY", KEY)
    monkeypatch.setenv("MANYSIGHT_CREDENTIAL_ACCESS_KEY", "resolve")
    source = client.post("/api/v1/sources", json=managed_rtsp()).json()
    sid = source["id"]
    bad = client.put(f"/api/v1/sources/{sid}", json={"connection": {"host": "rtsp://bad"}, "clear_credentials": True})
    assert bad.status_code == 422
    resolved = client.get(f"/api/v1/sources/{sid}/connection", headers={"X-ManySight-Credential-Key": "resolve"}).json()
    assert resolved["connection"]["password"] == "camera-pass"


def test_external_secret_mode_remains_supported(client, monkeypatch):
    monkeypatch.setenv("MANYSIGHT_CREDENTIAL_ACCESS_KEY", "resolve")
    source = client.post("/api/v1/sources", json={
        "name": "External", "kind": "http", "connection_management": "external_secret",
        "locator": {"local_secret_ref": "CAMERA_URL"},
    }).json()
    resolved = client.get(
        f"/api/v1/sources/{source['id']}/connection",
        headers={"X-ManySight-Credential-Key": "resolve"},
    ).json()
    assert resolved["connection"] == {"local_secret_ref": "CAMERA_URL"}


def test_source_delete_removes_ciphertext(client, isolated_db, monkeypatch):
    monkeypatch.setenv("MANYSIGHT_CREDENTIAL_KEY", KEY)
    source = client.post("/api/v1/sources", json=managed_rtsp()).json()
    assert client.delete(f"/api/v1/sources/{source['id']}").status_code == 200
    assert isolated_db.q("SELECT * FROM source_credentials") == []


def test_wrong_encryption_key_cannot_decrypt_existing_credentials(client, monkeypatch):
    monkeypatch.setenv("MANYSIGHT_CREDENTIAL_KEY", KEY)
    monkeypatch.setenv("MANYSIGHT_CREDENTIAL_ACCESS_KEY", "resolve")
    source = client.post("/api/v1/sources", json=managed_rtsp()).json()
    monkeypatch.setenv("MANYSIGHT_CREDENTIAL_KEY", base64.urlsafe_b64encode(b"x" * 32).decode())
    response = client.get(
        f"/api/v1/sources/{source['id']}/connection",
        headers={"X-ManySight-Credential-Key": "resolve"},
    )
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
    monkeypatch.setenv("MANYSIGHT_CREDENTIAL_ACCESS_KEY", "resolve")
    source = client.post("/api/v1/sources", json=managed_rtsp()).json()
    updated = client.put(f"/api/v1/sources/{source['id']}", json={
        "connection": {"host": "10.0.0.9", "port": 8554, "path": "/new", "transport": "udp"},
    })
    assert updated.status_code == 200
    resolved = client.get(
        f"/api/v1/sources/{source['id']}/connection",
        headers={"X-ManySight-Credential-Key": "resolve"},
    ).json()
    assert resolved["connection"]["host"] == "10.0.0.9"
    assert resolved["connection"]["password"] == "camera-pass"
