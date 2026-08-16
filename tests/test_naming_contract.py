"""The public naming contract: environment variables, headers, paths, enum value.

These are the names an operator types, a worker sends and a database stores. The
pre-release rename changed all of them at once and deliberately kept no aliases,
so each one is pinned here — both that the ManySight name works, and that the
previous name has no hidden effect. A silent fallback would defeat the rename.
"""
from __future__ import annotations

import base64
import importlib
import os
import sys

import pytest

from test_branding_audit import spelling

KEY = base64.urlsafe_b64encode(b"k" * 32).decode()
# The retired managed-connection value, assembled so this file does not
# reintroduce the name the repository audit forbids.
RETIRED_MANAGED = spelling() + "_managed"


# ---------------------------------------------------------------------------
# environment variables
# ---------------------------------------------------------------------------

def test_the_data_directory_and_database_file_are_manysight_named(tmp_path, monkeypatch):
    monkeypatch.setenv("MANYSIGHT_DATA", str(tmp_path))
    from server import db
    reloaded = importlib.reload(db)
    try:
        assert reloaded.DATA_DIR == str(tmp_path)
        assert reloaded.DB_PATH == os.path.join(str(tmp_path), "manysight.db")
        reloaded.init_db()
        assert (tmp_path / "manysight.db").is_file()
        # A fresh install must not create anything under the previous name.
        assert [item.name for item in tmp_path.iterdir() if "lens" in item.name.lower()] == []
    finally:
        monkeypatch.delenv("MANYSIGHT_DATA", raising=False)
        importlib.reload(db)


def test_the_api_key_is_read_from_the_manysight_variable(tmp_path, monkeypatch, isolated_db):
    monkeypatch.setenv("MANYSIGHT_API_KEY", "secret-key")
    from fastapi.testclient import TestClient
    module = importlib.reload(sys.modules["server.app"]) if "server.app" in sys.modules \
        else importlib.import_module("server.app")
    try:
        with TestClient(module.app) as client:
            assert client.get("/api/v1/sources").status_code == 401
            assert client.get("/api/v1/sources",
                              headers={"X-API-Key": "secret-key"}).status_code == 200
    finally:
        monkeypatch.delenv("MANYSIGHT_API_KEY", raising=False)
        importlib.reload(module)


@pytest.mark.parametrize("variable,module_path,attribute,value,expected", [
    ("MANYSIGHT_ALERT_POLL_INTERVAL_S", "server.app", "ALERT_POLL_INTERVAL_S", "42", 42.0),
    ("MANYSIGHT_LIVE_TICK_INTERVAL_S", "server.services.realtime", "TICK_INTERVAL_S", "0.02", 0.02),
    ("MANYSIGHT_SQLITE_SYNCHRONOUS", "server.db", "SYNCHRONOUS", "FULL", "FULL"),
])
def test_runtime_tuning_variables_use_the_manysight_prefix(
        variable, module_path, attribute, value, expected, monkeypatch, isolated_db):
    monkeypatch.setenv(variable, value)
    module = importlib.import_module(module_path)
    try:
        assert getattr(importlib.reload(module), attribute) == expected
    finally:
        monkeypatch.delenv(variable, raising=False)
        importlib.reload(module)


def test_the_mcp_adapter_reads_manysight_variables(monkeypatch):
    monkeypatch.setenv("MANYSIGHT_URL", "http://example.invalid:9999")
    monkeypatch.setenv("MANYSIGHT_MCP_LEGACY_TOOLS", "1")
    import mcp_server.server as mcp_server
    reloaded = importlib.reload(mcp_server)
    try:
        assert reloaded.BASE == "http://example.invalid:9999"
        assert reloaded.REST_BASE.startswith("http://example.invalid:9999")
        assert reloaded.LEGACY_TOOL_MODE is True
    finally:
        monkeypatch.delenv("MANYSIGHT_URL", raising=False)
        monkeypatch.delenv("MANYSIGHT_MCP_LEGACY_TOOLS", raising=False)
        importlib.reload(mcp_server)


def test_the_credential_key_variable_is_manysight_named(monkeypatch):
    from server.services import credentials
    assert credentials.KEY_ENV == "MANYSIGHT_CREDENTIAL_KEY"
    monkeypatch.delenv("MANYSIGHT_CREDENTIAL_KEY", raising=False)
    assert credentials.key_configured() is False
    monkeypatch.setenv("MANYSIGHT_CREDENTIAL_KEY", KEY)
    assert credentials.key_configured() is True
    payload = {"password": "do-not-leak"}
    envelope = credentials.encrypt(payload)
    assert credentials.decrypt(envelope) == payload
    assert "do-not-leak" not in envelope


# ---------------------------------------------------------------------------
# HTTP headers
# ---------------------------------------------------------------------------

def test_privileged_connection_resolution_uses_the_manysight_header(client, monkeypatch):
    monkeypatch.setenv("MANYSIGHT_CREDENTIAL_ACCESS_KEY", "resolve-me")
    source_id = client.post("/api/v1/sources", json={
        "name": "Managed", "kind": "http", "connection_management": "manysight_managed",
        "connection": {"url": "http://cam.internal/stream.mjpg"}}).json()["id"]

    assert client.get(f"/api/v1/sources/{source_id}/connection").status_code == 401
    allowed = client.get(f"/api/v1/sources/{source_id}/connection",
                         headers={"X-ManySight-Credential-Key": "resolve-me"})
    assert allowed.status_code == 200
    assert allowed.json()["connection"]["url"] == "http://cam.internal/stream.mjpg"


def test_the_demo_session_header_is_manysight_named(client, tmp_path, monkeypatch):
    """The demo guard routes on the ManySight header, and only that one."""
    from server.services import demo_runtime
    demo_db = tmp_path / "session.db"
    from server import db
    db.init_db(str(demo_db))
    monkeypatch.setattr(demo_runtime, "session_database",
                        lambda session_id: str(demo_db) if session_id == "abc" else None)

    client.post("/api/v1/sources", json={"name": "Real camera", "kind": "webcam"})
    assert len(client.get("/api/v1/sources").json()) == 1
    # Routed into the isolated workspace, which has no sources of its own.
    routed = client.get("/api/v1/sources", headers={"X-ManySight-Demo-Session": "abc"})
    assert routed.status_code == 200 and routed.json() == []
    # An unknown session is rejected rather than silently using the real one.
    assert client.get("/api/v1/sources",
                      headers={"X-ManySight-Demo-Session": "nope"}).status_code == 409


def test_the_dashboard_sends_the_manysight_header_and_storage_keys():
    import re
    api_js = open("dashboard/src/api.js", encoding="utf-8").read()
    assert '"X-ManySight-Demo-Session"' in api_js
    assert '"manysight_api_key"' in api_js
    assert '"manysight_demo_session"' in api_js
    assert not re.search(r"stor" + r"e" + r"[ _.-]?lens", api_js, re.IGNORECASE)


# ---------------------------------------------------------------------------
# persisted enum value
# ---------------------------------------------------------------------------

def test_the_managed_connection_value_is_manysight_named(client):
    created = client.post("/api/v1/sources", json={
        "name": "Managed", "kind": "http", "connection_management": "manysight_managed",
        "connection": {"url": "http://cam.internal/stream.mjpg"}})
    assert created.status_code == 201, created.text
    assert created.json()["connection_management"] == "manysight_managed"

    rejected = client.post("/api/v1/sources", json={
        "name": "Old spelling", "kind": "http",
        "connection_management": RETIRED_MANAGED,
        "connection": {"url": "http://cam.internal/stream.mjpg"}})
    assert rejected.status_code == 422, "the previous enum spelling must not be accepted"


def test_a_pre_release_database_normalizes_the_managed_connection_value(uninitialized_db):
    """A copied-forward database keeps working without naming the old value.

    `connection_management` has exactly two values, so init_db normalizes by that
    invariant: anything that is not `external_secret` is the managed mode.
    """
    db = uninitialized_db
    db.init_db()
    legacy_value = RETIRED_MANAGED
    db.ex("INSERT INTO sources (name,kind,connection_management,connection_config_json,created_at) "
          "VALUES (?,?,?,?,?)", ("Copied camera", "http", legacy_value,
                                 '{"url": "http://cam.internal/s.mjpg"}', 0.0))
    db.ex("INSERT INTO sources (name,kind,connection_management,locator_json,created_at) "
          "VALUES (?,?,?,?,?)", ("External camera", "rtsp", "external_secret",
                                 '{"local_secret_ref": "CAM_URL"}', 0.0))

    db.init_db()

    rows = {row["name"]: row["connection_management"]
            for row in db.q("SELECT name, connection_management FROM sources")}
    assert rows["Copied camera"] == "manysight_managed"
    assert rows["External camera"] == "external_secret", "external sources are left alone"


# ---------------------------------------------------------------------------
# SDK
# ---------------------------------------------------------------------------

def test_the_sdk_module_and_class_are_manysight_named():
    sys.path.insert(0, "sdk/python")
    import manysight

    assert manysight.ManySight.__name__ == "ManySight"
    client = manysight.ManySight("http://localhost:8000", api_key="k")
    assert client.base == "http://localhost:8000/api/v1"
    # The credential access key falls back to the API key, from the ManySight variable.
    assert client.credential_access_key == "k"
    assert not hasattr(manysight, spelling(case=str.title)), \
        "no compatibility alias may survive"
    assert not os.path.exists(f"sdk/python/{spelling()}.py")


def test_the_sdk_credential_header_is_manysight_named(monkeypatch):
    sys.path.insert(0, "sdk/python")
    from manysight import ManySight

    sent = {}

    class FakeResponse:
        ok = True
        status_code = 200

        @staticmethod
        def json():
            return {"connection": {}}

    client = ManySight(credential_access_key="resolve")
    monkeypatch.setattr(client.session, "get",
                        lambda url, headers=None, timeout=None: sent.update(headers or {})
                        or FakeResponse())
    client.get_source_connection(1)
    assert "X-ManySight-Credential-Key" in sent
    assert sent["X-ManySight-Credential-Key"] == "resolve"
