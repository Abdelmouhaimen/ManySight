"""Measurement aggregation respecting value_kind (services/derive.py:aggregate_measurement)."""
import pytest

from server.services import derive


def test_gauge_latest_and_never_summed():
    rows = [{"ts": t, "value": v, "value_kind": "gauge"} for t, v in ((0, 3), (10, 5), (20, 4))]
    agg = derive.aggregate_measurement(rows)
    assert agg["latest"] == 4
    assert agg["sum"] is None  # gauge samples must never be summed


def test_gauge_average_and_extremes():
    rows = [{"ts": t, "value": v, "value_kind": "gauge"} for t, v in ((0, 2), (10, 4), (20, 6))]
    agg = derive.aggregate_measurement(rows)
    assert agg["average"] == pytest.approx(4.0)
    assert agg["minimum"] == 2
    assert agg["maximum"] == 6


def test_delta_summed_and_rated():
    rows = [{"ts": t, "value": v, "value_kind": "delta"} for t, v in ((0, 1), (10, 2), (20, 3))]
    agg = derive.aggregate_measurement(rows)
    assert agg["sum"] == 6
    assert agg["rate"] == pytest.approx(6 / 20)


def test_cumulative_rate_ignores_negative_deltas():
    rows = [{"ts": t, "value": v, "value_kind": "cumulative"} for t, v in ((0, 10), (10, 20), (20, 30))]
    agg = derive.aggregate_measurement(rows)
    assert agg["rate"] == pytest.approx(20 / 20)  # (20-10)+(30-20) = 20 over 20s
    assert agg["sum"] is None


def test_cumulative_counter_reset_does_not_go_negative():
    """A worker restart resets the counter to a small value; the reset step
    itself must not subtract from the accumulated increase."""
    rows = [{"ts": t, "value": v, "value_kind": "cumulative"} for t, v in ((0, 100), (10, 150), (20, 5), (30, 25))]
    agg = derive.aggregate_measurement(rows)
    # (150-100) + (reset ignored) + (25-5) = 50 + 20 = 70, never negative
    assert agg["rate"] == pytest.approx(70 / 30)
    assert agg["rate"] >= 0


def test_empty_series_returns_none_fields():
    agg = derive.aggregate_measurement([])
    assert agg["latest"] is None
    assert agg["samples"] == 0


def test_single_sample_has_no_rate():
    rows = [{"ts": 0, "value": 5, "value_kind": "delta"}]
    agg = derive.aggregate_measurement(rows)
    assert agg["rate"] is None
    assert agg["latest"] == 5
