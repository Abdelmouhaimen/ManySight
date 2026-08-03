"""Route-shape regression tests for the observations router: /observations/latest
must never be shadowed by /observations/{observation_id}. A dynamic single-segment
path parameter route registered before a static path with the same prefix swallows
it (FastAPI/Starlette matches routes in registration order), so this file locks in
both the runtime behavior and the registration order itself."""
from helpers import make_detection


def test_latest_route_returns_200_with_no_data(client):
    response = client.get("/api/v1/observations/latest")
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"detection", "measurement", "state"}
    assert body["detection"]["entities"] == []
    assert body["measurement"]["series"] == []
    assert body["state"]["series"] == []


def test_latest_route_per_kind(client, source_id):
    body = {"observations": [{
        "schema_version": 2, "observation_id": "latest-m1", "kind": "measurement",
        "timestamp": 1000.0, "source_id": source_id, "name": "queue_length", "value": 4,
    }]}
    client.post("/api/v1/observations/batch", json=body)
    response = client.get("/api/v1/observations/latest", params={"kind": "measurement", "since": 0})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["kind"] == "measurement"
    assert result["series"][0]["name"] == "queue_length"


def test_latest_route_never_enters_id_route(client):
    """Before the fix this returned 422 with a path-param int_parsing error
    because /observations/{observation_id} matched "latest" as observation_id."""
    response = client.get("/api/v1/observations/latest")
    assert response.status_code == 200
    detail = response.json()
    assert "detail" not in detail or "int_parsing" not in str(detail.get("detail"))


def test_numeric_observation_id_still_resolves(client, source_id):
    body = {"observations": [make_detection(source_id, "route-obs-1", 1000.0, point_px=(500, 400))]}
    client.post("/api/v1/observations/batch", json=body)
    listing = client.get("/api/v1/observations", params={"source_id": source_id}).json()
    obs_id = listing["observations"][0]["id"]
    response = client.get(f"/api/v1/observations/{obs_id}")
    assert response.status_code == 200, response.text
    assert response.json()["id"] == obs_id


def test_unknown_numeric_observation_id_returns_404(client):
    response = client.get("/api/v1/observations/999999")
    assert response.status_code == 404


def test_non_integer_observation_id_returns_422(client):
    response = client.get("/api/v1/observations/not-a-number")
    assert response.status_code == 422


def test_contract_route_not_shadowed(client):
    """/observations/contract is the pre-existing known-good pattern: registered
    ahead of /observations/{observation_id} in the file, so it must resolve to
    the contract payload, not a failed int-parse of "contract"."""
    response = client.get("/api/v1/observations/contract")
    assert response.status_code == 200
    assert "kinds" in response.json()


def test_static_observation_routes_registered_before_dynamic_id_route():
    """Structural guard against regressing the fix: any static /observations/*
    route must be registered before the /observations/{observation_id} catch-all
    in the router's own route list, regardless of what gets added later. Checked
    against the plain APIRouter (not the app-level route table), since FastAPI/
    Starlette may apply further routing optimizations above the router that are
    not a stable surface to assert on."""
    from server.routers import observations
    paths = [r.path for r in observations.router.routes]
    id_route_index = paths.index("/observations/{observation_id}")
    static_paths = [p for p in paths if p != "/observations/{observation_id}"]
    for path in static_paths:
        assert paths.index(path) < id_route_index, (
            f"{path} is registered after /observations/{{observation_id}} and will be shadowed by it"
        )
