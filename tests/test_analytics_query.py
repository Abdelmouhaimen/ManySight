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


def test_time_grouped_active_entities_preserves_exact_detection_timestamps(client, calibrated_source):
    client.post("/api/v1/observations/batch", json={"observations": [
        make_detection(calibrated_source, "active-early-a", 1000.0, entity_id="a"),
        make_detection(calibrated_source, "active-early-b", 1000.0, entity_id="b"),
        make_detection(calibrated_source, "active-late-a", 4000.0, entity_id="a"),
    ]})
    response = client.post("/api/v1/analytics/query", json={
        "subject": "detection", "measures": ["active_entities"],
        "filters": {"entity_types": ["person"]},
        "grouping": {"primary": "time", "bucket": "1h"},
        "range": {"since": 0, "until": 7200},
    })
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["rows"] == [
        {"t": 1000.0, "active_entities": 2},
        {"t": 4000.0, "active_entities": 1},
    ]
    assert result["metadata"]["active_entity_semantics"] == "instantaneous camera/entity-type count at each exact producer timestamp"
    assert "window_s" not in result["metadata"]


def test_time_grouped_active_entities_does_not_accumulate_sequential_track_ids(client, calibrated_source):
    client.post("/api/v1/observations/batch", json={"observations": [
        make_detection(calibrated_source, "sequential-a", 1000.0, entity_id="a"),
        make_detection(calibrated_source, "sequential-a-duplicate", 1000.0, entity_id="a"),
        make_detection(calibrated_source, "sequential-b", 1100.0, entity_id="b"),
        make_detection(calibrated_source, "sequential-c", 1200.0, entity_id="c"),
    ]})
    response = client.post("/api/v1/analytics/query", json={
        "subject": "detection", "measures": ["active_entities"],
        "filters": {"entity_types": ["person"]},
        "grouping": {"primary": "time", "bucket": "1h"},
        "range": {"since": 0, "until": 3600},
    })
    assert response.status_code == 200, response.text
    result = response.json()
    assert [row["t"] for row in result["rows"]] == [1000.0, 1100.0, 1200.0]
    assert [row["active_entities"] for row in result["rows"]] == [1, 1, 1]


def test_processed_frame_counts_preserve_exact_zero_timestamps(client, calibrated_source):
    client.post("/api/v1/observations/batch", json={"observations": [
        make_measurement(
            calibrated_source, "frame-zero-a", 1000.1, "detection_frame_count", 0,
            label="person", unit="tracks",
        ),
        make_measurement(
            calibrated_source, "frame-zero-b", 1001.1, "detection_frame_count", 0,
            label="person", unit="tracks",
        ),
    ]})
    response = client.post("/api/v1/analytics/query", json={
        "subject": "detection", "measures": ["active_entities"],
        "filters": {"entity_types": ["person"], "source_ids": [calibrated_source]},
        "grouping": {"primary": "time"},
        "range": {"since": 1000, "until": 1002},
    })
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["rows"] == [
        {"t": 1000.1, "active_entities": 0},
        {"t": 1001.1, "active_entities": 0},
    ]
    assert result["metadata"]["zero_semantics"].startswith("zero only when the source explicitly")
    assert "window_s" not in result["metadata"]


def test_frame_count_is_authoritative_at_its_exact_timestamp(client, calibrated_source):
    client.post("/api/v1/observations/batch", json={"observations": [
        make_detection(calibrated_source, "window-a", 1000.1, entity_id="a"),
        make_detection(calibrated_source, "window-a-repeat", 1000.1, entity_id="a"),
        make_detection(calibrated_source, "window-b", 1000.1, entity_id="b"),
        make_measurement(
            calibrated_source, "window-marker", 1000.1, "detection_frame_count", 7,
            label="person", unit="tracks",
        ),
    ]})
    response = client.post("/api/v1/analytics/query", json={
        "subject": "detection", "measures": ["active_entities"],
        "filters": {"entity_types": ["person"], "source_ids": [calibrated_source]},
        "grouping": {"primary": "time"},
        "range": {"since": 1000, "until": 1001},
    })
    assert response.status_code == 200, response.text
    assert response.json()["rows"] == [{"t": 1000.1, "active_entities": 7}]


def test_close_frame_timestamps_are_not_merged(client, calibrated_source):
    client.post("/api/v1/observations/batch", json={"observations": [
        make_measurement(
            calibrated_source, "close-frame-a", 1000.10, "detection_frame_count", 1,
            label="person", unit="tracks",
        ),
        make_measurement(
            calibrated_source, "close-frame-b", 1000.20, "detection_frame_count", 2,
            label="person", unit="tracks",
        ),
    ]})
    response = client.post("/api/v1/analytics/query", json={
        "subject": "detection", "measures": ["active_entities"],
        "filters": {"entity_types": ["person"], "source_ids": [calibrated_source]},
        "grouping": {"primary": "time", "bucket": "1h"},
        "range": {"since": 1000, "until": 1001},
    })
    assert response.status_code == 200, response.text
    assert response.json()["rows"] == [
        {"t": 1000.1, "active_entities": 1},
        {"t": 1000.2, "active_entities": 2},
    ]


def test_same_timestamp_frame_count_observations_are_not_summed(client, calibrated_source):
    client.post("/api/v1/observations/batch", json={"observations": [
        make_measurement(
            calibrated_source, "same-time-a", 1000.1, "detection_frame_count", 1,
            label="person", unit="tracks",
        ),
        make_measurement(
            calibrated_source, "same-time-b", 1000.1, "detection_frame_count", 2,
            label="person", unit="tracks",
        ),
    ]})
    response = client.post("/api/v1/analytics/query", json={
        "subject": "detection", "measures": ["active_entities"],
        "filters": {"entity_types": ["person"], "source_ids": [calibrated_source]},
        "grouping": {"primary": "time"},
        "range": {"since": 1000, "until": 1001},
    })
    assert response.status_code == 200, response.text
    assert response.json()["rows"] == [
        {"t": 1000.1, "active_entities": 1},
        {"t": 1000.1, "active_entities": 2},
    ]


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


def test_empty_result_for_multiple_unmatched_filters(client, calibrated_source):
    response = client.post("/api/v1/analytics/query", json={
        "subject": "measurement", "measures": ["latest"],
        "filters": {"measurement_names": ["nonexistent_a", "nonexistent_b"]},
        "range": {"since": 0, "until": 2000},
    })
    assert response.status_code == 200
    assert response.json()["rows"] == []


def test_mixed_existing_and_nonexistent_names_only_returns_real_rows(client, calibrated_source):
    client.post("/api/v1/observations/batch", json={"observations": [
        make_measurement(calibrated_source, "q-mix-1", 1000.0, "queue_length", 3),
        make_measurement(calibrated_source, "q-mix-2", 1030.0, "queue_length", 5),
    ]})
    response = client.post("/api/v1/analytics/query", json={
        "subject": "measurement", "measures": ["latest", "average"],
        "filters": {"measurement_names": ["queue_length", "nonexistent"]},
        "range": {"since": 0, "until": 2000},
    })
    assert response.status_code == 200
    result = response.json()
    assert [row["measurement_name"] for row in result["rows"]] == ["queue_length"]
    # gauge semantics untouched by the empty-row fix: averaged, not summed
    assert result["rows"][0]["latest"] == 5
    assert result["rows"][0]["average"] == 4


def test_grouped_query_with_unmatched_filter_returns_no_buckets(client, calibrated_source):
    response = client.post("/api/v1/analytics/query", json={
        "subject": "measurement", "measures": ["average"],
        "filters": {"measurement_names": ["nonexistent"]},
        "grouping": {"primary": "time", "bucket": "1h"},
        "range": {"since": 0, "until": 3600 * 3},
    })
    assert response.status_code == 200
    assert response.json()["rows"] == []


def test_no_filter_no_data_still_returns_empty_rows(client):
    """The pre-existing no-filter path this fix must stay consistent with."""
    response = client.post("/api/v1/analytics/query", json={
        "subject": "measurement", "measures": ["latest"],
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


def test_capabilities_records_distinct_detection_entity_types(client, calibrated_source):
    client.post("/api/v1/observations/batch", json={"observations": [
        make_detection(calibrated_source, "type-person", 1000.0, entity_type="person"),
        make_detection(calibrated_source, "type-cart", 1001.0, entity_type="cart"),
        make_detection(calibrated_source, "type-cart-2", 1002.0, entity_type="cart"),
    ]})
    response = client.get("/api/v1/analytics/capabilities")
    assert response.status_code == 200
    assert response.json()["entity_types"] == ["cart", "person"]


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
