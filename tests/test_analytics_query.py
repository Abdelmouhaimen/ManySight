"""Unified analytics query engine (server/routers/analytics_query.py)."""
from helpers import make_detection, make_measurement


def test_kpi_query_active_entities(client, calibrated_source):
    client.post("/api/v1/observations/batch", json={"observations": [
        make_detection(calibrated_source, "q-1", 1000.0, entity_id="a", point_px=(100, 100)),
        make_detection(calibrated_source, "q-2", 1000.0, entity_id="b", point_px=(200, 200)),
    ]})
    response = client.post("/api/v1/analytics/query", json={
        "subject": "detection", "measures": ["distinct_entities"],
        "range": {"since": 0, "until": 2000},
    })
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["shape"] == "scalar"
    assert result["rows"][0]["distinct_entities"] == 2


def test_time_grouping_returns_buckets(client, calibrated_source):
    client.post("/api/v1/observations/batch", json={"observations": [
        make_detection(calibrated_source, "q-3", 1000.0, point_px=(100, 100)),
    ]})
    response = client.post("/api/v1/analytics/query", json={
        "subject": "detection", "measures": ["observations"],
        "grouping": {"primary": "time", "bucket": "1h"},
        "range": {"since": 0, "until": 3600 * 3},
    })
    result = response.json()
    assert result["shape"] == "timeseries"
    assert sum(row["observations"] for row in result["rows"]) == 1


def test_zone_grouping(client, calibrated_source):
    zone = client.post("/api/v1/zones", json={
        "name": "Checkout",
        "polygon": [{"x": 4, "y": 3}, {"x": 6, "y": 3}, {"x": 6, "y": 5}, {"x": 4, "y": 5}],
    }).json()
    client.post("/api/v1/observations/batch", json={"observations": [
        make_detection(calibrated_source, "q-4", 1000.0, point_px=(500, 400)),
    ]})
    response = client.post("/api/v1/analytics/query", json={
        "subject": "detection", "measures": ["observations"],
        "grouping": {"primary": "zone"}, "filters": {"zone_ids": [zone["id"]]},
        "range": {"since": 0, "until": 2000},
    })
    result = response.json()
    assert result["shape"] == "categorical"
    assert result["rows"][0]["zone_id"] == zone["id"]
    assert result["rows"][0]["observations"] == 1


def test_invalid_measure_for_subject_rejected(client):
    response = client.post("/api/v1/analytics/query", json={
        "subject": "measurement", "measures": ["visits"],  # a detection measure
    })
    assert response.status_code == 422


def test_empty_result_for_unmatched_filters(client, calibrated_source):
    response = client.post("/api/v1/analytics/query", json={
        "subject": "measurement", "measures": ["latest"],
        "filters": {"measurement_names": ["nonexistent"]},
        "range": {"since": 0, "until": 2000},
    })
    assert response.status_code == 200
    assert response.json()["rows"] == []


def test_capabilities_endpoint_lists_measures_per_subject(client):
    response = client.get("/api/v1/analytics/capabilities")
    assert response.status_code == 200
    caps = response.json()
    assert "active_entities" in caps["measures_by_subject"]["detection"]
    assert "rate" in caps["measures_by_subject"]["measurement"]
    assert "duration" in caps["measures_by_subject"]["state"]


def test_label_filter_narrows_results(client, calibrated_source):
    client.post("/api/v1/observations/batch", json={"observations": [
        make_detection(calibrated_source, "q-5", 1000.0, entity_id="a", point_px=(100, 100), label="customer"),
        make_detection(calibrated_source, "q-6", 1000.0, entity_id="b", point_px=(200, 200), label="staff"),
    ]})
    response = client.post("/api/v1/analytics/query", json={
        "subject": "detection", "measures": ["distinct_entities"],
        "filters": {"labels": ["customer"]}, "range": {"since": 0, "until": 2000},
    })
    assert response.json()["rows"][0]["distinct_entities"] == 1


def test_split_by_is_validated_even_though_not_every_combination_computes_it(client):
    """KNOWN LIMITATION: grouping.split_by is validated against the vocabulary
    (this call succeeds) but is not wired into every measure/grouping
    combination in this implementation pass -- e.g. zone-grouped detection
    measures do not yet further break down by split_by. This test documents
    the current boundary rather than asserting full behavior."""
    response = client.post("/api/v1/analytics/query", json={
        "subject": "detection", "measures": ["distinct_entities"],
        "grouping": {"primary": "zone", "split_by": ["label"]},
        "range": {"since": 0, "until": 2000},
    })
    assert response.status_code == 200


def test_previous_period_comparison(client, calibrated_source):
    client.post("/api/v1/observations/batch", json={"observations": [
        make_measurement(calibrated_source, "q-7", 1000.0, "queue_length", 5),
    ]})
    response = client.post("/api/v1/analytics/query", json={
        "subject": "measurement", "measures": ["latest"],
        "filters": {"measurement_names": ["queue_length"]},
        "range": {"since": 500, "until": 1500}, "comparison": {"mode": "previous_period"},
    })
    result = response.json()
    assert "comparison" in result
    assert result["comparison"]["since"] == 500 - (1500 - 500)
