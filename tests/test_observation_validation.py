"""Observation validation: POST /api/v1/observations/batch."""
from helpers import make_detection


def test_valid_detection(client, calibrated_source):
    body = {"observations": [make_detection(calibrated_source, "obs-1", 1000.0, point_px=(500, 400))]}
    response = client.post("/api/v1/observations/batch", json=body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["accepted"] == 1
    assert result["rejected"] == []


def test_valid_measurement(client, source_id):
    body = {"observations": [{
        "schema_version": 2, "observation_id": "obs-m1", "kind": "measurement",
        "timestamp": 1000.0, "source_id": source_id, "name": "queue_length",
        "value": 5, "value_kind": "gauge",
    }]}
    response = client.post("/api/v1/observations/batch", json=body)
    assert response.status_code == 200, response.text
    assert response.json()["accepted"] == 1


def test_valid_state(client, source_id):
    body = {"observations": [{
        "schema_version": 2, "observation_id": "obs-s1", "kind": "state",
        "timestamp": 1000.0, "source_id": source_id, "name": "door_state", "label": "closed",
    }]}
    response = client.post("/api/v1/observations/batch", json=body)
    assert response.status_code == 200, response.text
    assert response.json()["accepted"] == 1


def test_missing_required_field_is_item_level_error(client, source_id):
    """A measurement with no value is rejected per-item, not a whole-batch 422 —
    the batch otherwise proceeds."""
    body = {"observations": [
        {"schema_version": 2, "observation_id": "obs-bad", "kind": "measurement",
         "timestamp": 1000.0, "source_id": source_id, "name": "queue_length"},
        {"schema_version": 2, "observation_id": "obs-good", "kind": "measurement",
         "timestamp": 1000.0, "source_id": source_id, "name": "queue_length", "value": 3},
    ]}
    response = client.post("/api/v1/observations/batch", json=body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["accepted"] == 1
    assert len(result["rejected"]) == 1
    assert result["rejected"][0]["error"] == "missing_required_field"


def test_invalid_numeric_value_rejected(client, source_id):
    body = {"observations": [{
        "schema_version": 2, "observation_id": "obs-nan", "kind": "measurement",
        "timestamp": 1000.0, "source_id": source_id, "name": "queue_length", "value": float("nan"),
    }]}
    response = client.post("/api/v1/observations/batch", json=body)
    # FastAPI/pydantic serializes NaN as a bare `NaN` token that most JSON
    # parsers (including the server's) reject at the transport layer before
    # this ever reaches validate_shape — assert it does not silently succeed.
    assert response.status_code != 200 or response.json()["accepted"] == 0


def test_duplicate_observation_id_is_idempotent_not_error(client, source_id):
    obs = {"schema_version": 2, "observation_id": "dup-1", "kind": "measurement",
          "timestamp": 1000.0, "source_id": source_id, "name": "queue_length", "value": 3}
    first = client.post("/api/v1/observations/batch", json={"observations": [obs]})
    second = client.post("/api/v1/observations/batch", json={"observations": [obs]})
    assert first.json()["accepted"] == 1
    assert second.json()["accepted"] == 0
    assert second.json()["duplicates"] == 1
    assert second.json()["rejected"] == []
    total = client.get("/api/v1/observations", params={"source_id": source_id})
    assert total.json()["total"] == 1


def test_batch_retry_idempotency_within_same_batch(client, source_id):
    obs = {"schema_version": 2, "observation_id": "dup-2", "kind": "measurement",
          "timestamp": 1000.0, "source_id": source_id, "name": "queue_length", "value": 3}
    response = client.post("/api/v1/observations/batch", json={"observations": [obs, obs]})
    result = response.json()
    assert result["accepted"] == 1
    assert result["duplicates"] == 1


def test_legacy_derived_kind_rejected(client, source_id):
    body = {"observations": [{
        "schema_version": 2, "observation_id": "legacy-1", "kind": "zone_enter",
        "timestamp": 1000.0, "source_id": source_id,
    }]}
    response = client.post("/api/v1/observations/batch", json=body)
    assert response.status_code == 200
    result = response.json()
    assert result["accepted"] == 0
    assert result["rejected"][0]["error"] == "legacy_derived_observation"


def test_zone_id_forbidden(client, source_id):
    body = {"observations": [{
        "schema_version": 2, "observation_id": "zoned-1", "kind": "detection",
        "timestamp": 1000.0, "source_id": source_id, "zone_id": 1,
    }]}
    response = client.post("/api/v1/observations/batch", json=body)
    result = response.json()
    assert result["accepted"] == 0
    assert result["rejected"][0]["error"] == "zone_resolution_forbidden"


def test_out_of_order_timestamps_all_accepted(client, source_id):
    body = {"observations": [
        {"schema_version": 2, "observation_id": "ooo-2", "kind": "measurement", "timestamp": 2000.0,
         "source_id": source_id, "name": "queue_length", "value": 3},
        {"schema_version": 2, "observation_id": "ooo-1", "kind": "measurement", "timestamp": 1000.0,
         "source_id": source_id, "name": "queue_length", "value": 2},
    ]}
    response = client.post("/api/v1/observations/batch", json=body)
    assert response.json()["accepted"] == 2


def test_oversized_batch_rejected(client, source_id):
    obs = {"schema_version": 2, "observation_id": "x", "kind": "measurement",
          "timestamp": 1000.0, "source_id": source_id, "name": "n", "value": 1}
    body = {"observations": [{**obs, "observation_id": f"x{i}"} for i in range(5001)]}
    response = client.post("/api/v1/observations/batch", json=body)
    assert response.status_code == 413


def test_unknown_source_rejected(client):
    body = {"observations": [{
        "schema_version": 2, "observation_id": "unk-1", "kind": "measurement",
        "timestamp": 1000.0, "source_id": 999999, "name": "n", "value": 1,
    }]}
    response = client.post("/api/v1/observations/batch", json=body)
    assert response.status_code == 404
