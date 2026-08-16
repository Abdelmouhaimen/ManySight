"""Shared pytest fixtures. Every test gets a fresh, isolated SQLite database —
`server/db.py`'s DATA_DIR/DB_PATH are module-level globals read at call time by
`connect()`, so monkeypatching them before `init_db()` gives true per-test
isolation without touching the real project data.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from server import db  # noqa: E402


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    """Point server.db at a fresh SQLite file for this test only."""
    monkeypatch.setattr(db, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
    yield db


@pytest.fixture
def uninitialized_db(tmp_path, monkeypatch):
    """Like isolated_db, but does NOT call init_db() -- for migration tests that
    need to hand-build a pre-migration schema at the target path first and then
    call db.init_db() themselves (possibly more than once)."""
    monkeypatch.setattr(db, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    yield db


@pytest.fixture
def app(isolated_db, monkeypatch):
    """The FastAPI app, imported fresh against the isolated DB. Auth is
    disabled by default; tests that need it set MANYSIGHT_API_KEY themselves
    before importing (see test_api_auth.py) since app.py reads it at import time."""
    monkeypatch.setenv("MANYSIGHT_API_KEY", "")
    monkeypatch.delenv("MANYSIGHT_CREDENTIAL_KEY", raising=False)
    # server.app runs db.init_db() and mounts dashboard/dist at import time;
    # importing it fresh each test would re-run module-level side effects
    # against whatever DATA_DIR is active *right now* (already monkeypatched
    # above), then leave the module cached for the rest of the session.
    if "server.app" in sys.modules:
        import importlib
        module = importlib.reload(sys.modules["server.app"])
    else:
        from server import app as module
    return module.app


@pytest.fixture
def client(app):
    from fastapi.testclient import TestClient
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def source_id(client):
    response = client.post("/api/v1/sources", json={"name": "Test cam", "kind": "webcam"})
    assert response.status_code == 201, response.text
    return response.json()["id"]


@pytest.fixture
def calibrated_source(client, source_id):
    """A source calibrated with a simple, exactly-computable 1:1 pixel->metre
    mapping (scale 0.01, i.e. 100px = 1m) so projected coordinates in tests are
    easy to predict by hand."""
    points = [
        {"px": {"x": 0, "y": 0}, "map": {"x": 0, "y": 0}},
        {"px": {"x": 1000, "y": 0}, "map": {"x": 10, "y": 0}},
        {"px": {"x": 1000, "y": 800}, "map": {"x": 10, "y": 8}},
        {"px": {"x": 0, "y": 800}, "map": {"x": 0, "y": 8}},
    ]
    response = client.put(f"/api/v1/sources/{source_id}/calibration", json={
        "points": points, "frame_w": 1000, "frame_h": 800,
    })
    assert response.status_code == 200, response.text
    return source_id
