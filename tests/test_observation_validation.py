"""Observation validation: POST /api/v1/observations/batch."""
import json

import pytest

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


def test_invalid_numeric_value_rejected_by_server(client, source_id):
    """Proves the server itself rejects a NaN value (enrich.py:validate_shape),
    not merely that some HTTP client library refuses to transmit one. Sent as
    a raw body (bypassing the test client's own JSON encoder -- see
    test_httpx_json_encoder_refuses_nan_before_the_request_is_sent below) using
    the bare `NaN` token Python's json module accepts as a non-standard
    extension on both the write and read side, so it actually reaches the
    server's request parser and then validate_shape."""
    raw = json.dumps({"observations": [{
        "schema_version": 2, "observation_id": "obs-nan", "kind": "measurement",
        "timestamp": 1000.0, "source_id": source_id, "name": "queue_length", "value": float("nan"),
    }]}, allow_nan=True).encode()
    assert b"NaN" in raw  # sanity: we're actually exercising the NaN path
    response = client.post(
        "/api/v1/observations/batch", content=raw, headers={"content-type": "application/json"},
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["accepted"] == 0
    assert result["rejected"][0]["error"] == "invalid_observation"
    assert "finite" in result["rejected"][0]["message"]


def test_httpx_json_encoder_refuses_nan_before_the_request_is_sent(client, source_id):
    """Documents a client-library limitation, not server behavior: passing
    `json=` (as any normal caller would) serializes via a strict encoder
    (`allow_nan=False`) that raises before the request is even sent, so a NaN
    sent this way never reaches the server's own validation at all."""
    body = {"observations": [{
        "schema_version": 2, "observation_id": "obs-nan-2", "kind": "measurement",
        "timestamp": 1000.0, "source_id": source_id, "name": "queue_length", "value": float("nan"),
    }]}
    with pytest.raises(ValueError, match="[Nn]ot JSON compliant|[Oo]ut of range"):
        client.post("/api/v1/observations/batch", json=body)


def test_validate_shape_rejects_nan_and_inf_directly():
    """Unit-tests the canonical numeric validator itself, independent of any
    HTTP client's JSON encoding behavior."""
    from server.services import enrich

    for bad in (float("nan"), float("inf"), float("-inf")):
        try:
            enrich.validate_shape({"value": bad})
        except ValueError as exc:
            assert "finite" in str(exc)
        else:
            raise AssertionError(f"validate_shape accepted non-finite value {bad!r}")


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
