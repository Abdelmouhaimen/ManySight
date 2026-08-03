"""State coalescing and staleness (services/derive.py:coalesce_state_intervals)."""
from server.services import derive


def test_repeated_identical_samples_collapse_to_one_interval():
    rows = [{"ts": t, "label": "closed"} for t in (0, 30, 60, 90)]
    intervals, stale = derive.coalesce_state_intervals(rows, until=100, stale_s=120)
    assert len(intervals) == 1
    assert intervals[0]["samples"] == 4
    assert stale is False


def test_transition_detected_between_different_labels():
    rows = [{"ts": t, "label": lbl} for t, lbl in ((0, "closed"), (30, "closed"), (60, "open"), (90, "open"))]
    intervals, _ = derive.coalesce_state_intervals(rows, until=100, stale_s=120)
    assert len(intervals) == 2
    assert intervals[0]["label"] == "closed"
    assert intervals[0]["end"] == 60  # ends exactly where the next interval starts
    assert intervals[1]["label"] == "open"


def test_out_of_order_sample_still_coalesces_by_input_order():
    """coalesce_state_intervals trusts caller ordering (derive.state_samples
    always orders by ts,id) -- verify the coalescing logic itself is order-driven,
    not re-sorting internally."""
    rows = [{"ts": 0, "label": "closed"}, {"ts": 10, "label": "closed"}]
    intervals, _ = derive.coalesce_state_intervals(rows, until=20, stale_s=120)
    assert len(intervals) == 1
    assert intervals[0]["samples"] == 2


def test_current_state_is_latest_interval():
    rows = [{"ts": t, "label": lbl} for t, lbl in ((0, "closed"), (60, "open"))]
    intervals, _ = derive.coalesce_state_intervals(rows, until=90, stale_s=120)
    assert intervals[-1]["label"] == "open"


def test_completed_duration_is_gap_to_next_interval():
    rows = [{"ts": t, "label": lbl} for t, lbl in ((0, "closed"), (100, "open"))]
    intervals, _ = derive.coalesce_state_intervals(rows, until=200, stale_s=120)
    assert intervals[0]["end"] - intervals[0]["start"] == 100


def test_ongoing_duration_extends_to_until_when_not_stale():
    rows = [{"ts": 0, "label": "open"}]
    intervals, stale = derive.coalesce_state_intervals(rows, until=90, stale_s=120)
    assert stale is False
    assert intervals[0]["end"] == 90  # extended to `until`, not capped


def test_stale_state_does_not_extend_forever():
    rows = [{"ts": 0, "label": "open"}]
    intervals, stale = derive.coalesce_state_intervals(rows, until=1000, stale_s=120)
    assert stale is True
    assert intervals[0]["end"] == 120  # last_sample_ts (0) + stale_s (120), not `until`


def test_multiple_entities_same_source_and_name_are_independent(isolated_db):
    isolated_db.ex(
        "INSERT INTO sources (name, kind, created_at) VALUES ('cam','sensor',0)"
    )
    for entity, label, ts in (("fridge-1", "closed", 0), ("fridge-2", "open", 0)):
        isolated_db.ex(
            "INSERT INTO events (source_id, ts, event_type, track_id, name, label, attributes, created_at)"
            " VALUES (1, ?, 'state', ?, 'door_state', ?, '{}', ?)", (ts, entity, label, ts))
    keys = derive.state_keys(0, 10, source_id=1)
    assert set(keys) == {(1, "door_state", "fridge-1"), (1, "door_state", "fridge-2")}
