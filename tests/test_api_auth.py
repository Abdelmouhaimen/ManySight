"""API key auth and public-reads behavior (server/app.py:api_key_guard)."""
import importlib
import sys

import pytest


@pytest.fixture
def keyed_app(isolated_db, monkeypatch):
    monkeypatch.setenv("STORELENS_API_KEY", "secret123")
    monkeypatch.setenv("STORELENS_PUBLIC_READS", "false")
    if "server.app" in sys.modules:
        module = importlib.reload(sys.modules["server.app"])
    else:
        from server import app as module
    return module.app


@pytest.fixture
def keyed_client(keyed_app):
    from fastapi.testclient import TestClient
    with TestClient(keyed_app) as test_client:
        yield test_client


def test_missing_api_key_rejected(keyed_client):
    response = keyed_client.get("/api/v1/sources")
    assert response.status_code == 401


def test_wrong_api_key_rejected(keyed_client):
    response = keyed_client.get("/api/v1/sources", headers={"X-API-Key": "wrong"})
    assert response.status_code == 401


def test_correct_api_key_accepted(keyed_client):
    response = keyed_client.get("/api/v1/sources", headers={"X-API-Key": "secret123"})
    assert response.status_code == 200


def test_health_endpoint_never_requires_key(keyed_client):
    response = keyed_client.get("/api/v1/health")
    assert response.status_code == 200


def test_query_param_key_also_accepted(keyed_client):
    response = keyed_client.get("/api/v1/sources?api_key=secret123")
    assert response.status_code == 200


@pytest.fixture
def public_read_app(isolated_db, monkeypatch):
    monkeypatch.setenv("STORELENS_API_KEY", "secret123")
    monkeypatch.setenv("STORELENS_PUBLIC_READS", "true")
    module = importlib.reload(sys.modules["server.app"]) if "server.app" in sys.modules \
        else __import__("server.app", fromlist=["app"])
    return module.app


def test_public_reads_allows_unauthenticated_get(public_read_app):
    from fastapi.testclient import TestClient
    with TestClient(public_read_app) as client:
        assert client.get("/api/v1/sources").status_code == 200


def test_public_reads_still_blocks_unauthenticated_write(public_read_app):
    from fastapi.testclient import TestClient
    with TestClient(public_read_app) as client:
        response = client.post("/api/v1/sources", json={"name": "x", "kind": "webcam"})
        assert response.status_code == 401


def test_cors_preflight_gets_headers_when_key_required(keyed_client):
    """Regression test for the fixed bug: CORSMiddleware must be outermost so a
    preflight OPTIONS request gets Access-Control-Allow-Origin even though the
    guard would otherwise 401 it (no X-API-Key on a preflight)."""
    response = keyed_client.options(
        "/api/v1/sources",
        headers={
            "Origin": "http://localhost:8000",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.headers.get("access-control-allow-origin") is not None
