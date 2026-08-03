"""Alert evaluation, especially the periodic ongoing-condition fix
(services/alert_engine.py:evaluate_ongoing) -- the central promise that a
loitering/over-capacity/stuck-state condition fires without waiting for
another observation to land in the same zone."""
from helpers import make_detection, make_state

from server import db
from server.services import alert_engine


def test_dwell_exceeds_ongoing_without_a_new_zone_event(isolated_db):
    """This is the bug the redesign explicitly asked to fix: previously,
    'still loitering' only re-evaluated when another event touched the same
    zone. evaluate_ongoing must find an open, over-threshold visit purely from
    `now`, with no batch and no new event required."""
    zone = isolated_db.ex("INSERT INTO zones (name, ztype, polygon_json, created_at, updated_at)"
                          " VALUES ('Checkout','checkout','[]',0,0)")
    isolated_db.ex(
        "INSERT INTO alert_rules (name, kind, params_json, cooldown_s, enabled, created_at)"
        " VALUES ('Loitering', 'dwell_exceeds', ?, 60, 1, 0)",
        (f'{{"zone_id": {zone}, "seconds": 60}}',),
    )
    now = 10000.0
    # Gaps of 40s stay within derive.MAX_GAP_S (45s) so these merge into one
    # confirmed session; the last sample (10s ago) keeps it "open" rather than
    # closed -- this is what a real tracker sampling every ~40s would produce.
    for ts in (now - 200, now - 160, now - 120, now - 80, now - 10):
        isolated_db.ex(
            "INSERT INTO events (source_id, ts, event_type, track_id, zone_id, attributes, created_at)"
            " VALUES (1, ?, 'detection', 'e1', ?, '{}', ?)", (ts, zone, ts))
    fired = alert_engine.evaluate_ongoing(now, {zone: "Checkout"})
    assert len(fired) == 1
    assert "e1" in fired[0]["message"]


def test_dwell_exceeds_ongoing_respects_cooldown(isolated_db):
    zone = isolated_db.ex("INSERT INTO zones (name, ztype, polygon_json, created_at, updated_at)"
                          " VALUES ('Checkout','checkout','[]',0,0)")
    isolated_db.ex(
        "INSERT INTO alert_rules (name, kind, params_json, cooldown_s, enabled, last_fired_at, created_at)"
        " VALUES ('Loitering', 'dwell_exceeds', ?, 300, 1, ?, 0)",
        (f'{{"zone_id": {zone}, "seconds": 60}}', 9990.0),
    )
    now = 10000.0  # only 10s since last_fired_at, cooldown is 300s
    for ts in (now - 200, now - 160, now - 120, now - 80, now - 10):
        isolated_db.ex(
            "INSERT INTO events (source_id, ts, event_type, track_id, zone_id, attributes, created_at)"
            " VALUES (1, ?, 'detection', 'e1', ?, '{}', ?)", (ts, zone, ts))
    fired = alert_engine.evaluate_ongoing(now, {zone: "Checkout"})
    assert fired == []


def test_occupancy_exceeds_ongoing(isolated_db):
    zone = isolated_db.ex("INSERT INTO zones (name, ztype, polygon_json, created_at, updated_at)"
                          " VALUES ('Entrance','entrance','[]',0,0)")
    isolated_db.ex(
        "INSERT INTO alert_rules (name, kind, params_json, cooldown_s, enabled, created_at)"
        " VALUES ('Crowd', 'occupancy_exceeds', ?, 60, 1, 0)",
        (f'{{"zone_id": {zone}, "count": 2, "window_s": 60}}',),
    )
    now = 10000.0
    for entity, ts in (("a", now - 30), ("b", now - 20), ("c", now - 10)):
        isolated_db.ex(
            "INSERT INTO events (source_id, ts, event_type, track_id, zone_id, attributes, created_at)"
            " VALUES (1, ?, 'detection', ?, ?, '{}', ?)", (ts, entity, zone, ts))
    fired = alert_engine.evaluate_ongoing(now, {zone: "Entrance"})
    assert len(fired) == 1
    assert "3 people" in fired[0]["message"]


def test_state_alert_ongoing_stuck_open(isolated_db):
    isolated_db.ex("INSERT INTO sources (name, kind, created_at) VALUES ('fridge','sensor',0)")
    isolated_db.ex(
        "INSERT INTO alert_rules (name, kind, params_json, cooldown_s, enabled, created_at)"
        " VALUES ('Fridge open', 'state_alert', ?, 60, 1, 0)",
        ('{"label":"open","name":"door_state","min_seconds":120,"source_id":1}',),
    )
    now = 10000.0
    for ts in (now - 200, now - 150, now - 100, now - 50):
        isolated_db.ex(
            "INSERT INTO events (source_id, ts, event_type, name, label, attributes, created_at)"
            " VALUES (1, ?, 'state', 'door_state', 'open', '{}', ?)", (ts, ts))
    fired = alert_engine.evaluate_ongoing(now, {})
    assert len(fired) == 1


def test_state_alert_ongoing_does_not_fire_on_stale_source(isolated_db):
    """A source that stopped reporting must not keep 'counting' toward a
    duration alert once its last sample is older than the staleness timeout."""
    isolated_db.ex("INSERT INTO sources (name, kind, created_at) VALUES ('fridge','sensor',0)")
    isolated_db.ex(
        "INSERT INTO alert_rules (name, kind, params_json, cooldown_s, enabled, created_at)"
        " VALUES ('Fridge open', 'state_alert', ?, 60, 1, 0)",
        ('{"label":"open","name":"door_state","min_seconds":120,"source_id":1}',),
    )
    now = 10000.0
    isolated_db.ex(
        "INSERT INTO events (source_id, ts, event_type, name, label, attributes, created_at)"
        " VALUES (1, ?, 'state', 'door_state', 'open', '{}', ?)", (now - 5000, now - 5000))
    fired = alert_engine.evaluate_ongoing(now, {})
    assert fired == []


def test_analysis_condition_requires_for_seconds_continuity(isolated_db):
    isolated_db.ex("INSERT INTO sources (name, kind, created_at) VALUES ('sensor','sensor',0)")
    isolated_db.ex(
        "INSERT INTO alert_rules (name, kind, analysis_json, condition_json, condition_state_json,"
        " cooldown_s, enabled, created_at) VALUES ('Queue spike', 'analysis_condition', ?, ?, '{}', 60, 1, 0)",
        ('{"subject":"measurement","measures":["latest"],"filters":{"measurement_names":["queue_length"]}}',
         '{"operator":">","value":10,"for_seconds":300,"window_s":900}'),
    )
    now = 10000.0
    isolated_db.ex(
        "INSERT INTO events (source_id, ts, event_type, name, value, value_kind, created_at)"
        " VALUES (1, ?, 'measurement', 'queue_length', 15, 'gauge', ?)", (now, now))
    # First poll: condition just started holding -- must not fire immediately.
    first = alert_engine.evaluate_ongoing(now, {})
    assert first == []
    rule = db.q1("SELECT condition_state_json FROM alert_rules WHERE name='Queue spike'")
    assert db.jload(rule["condition_state_json"], {}).get("true_since") == now
    # 301s later, still above threshold -- now it should fire.
    later = now + 301
    isolated_db.ex(
        "INSERT INTO events (source_id, ts, event_type, name, value, value_kind, created_at)"
        " VALUES (1, ?, 'measurement', 'queue_length', 15, 'gauge', ?)", (later, later))
    second = alert_engine.evaluate_ongoing(later, {})
    assert len(second) == 1


def test_analysis_condition_resets_when_condition_stops_holding(isolated_db):
    isolated_db.ex("INSERT INTO sources (name, kind, created_at) VALUES ('sensor','sensor',0)")
    isolated_db.ex(
        "INSERT INTO alert_rules (name, kind, analysis_json, condition_json, condition_state_json,"
        " cooldown_s, enabled, created_at) VALUES ('Queue spike', 'analysis_condition', ?, ?, ?, 60, 1, 0)",
        ('{"subject":"measurement","measures":["latest"],"filters":{"measurement_names":["queue_length"]}}',
         '{"operator":">","value":10,"for_seconds":300,"window_s":900}',
         '{"true_since": 9000.0}'),
    )
    now = 10000.0
    isolated_db.ex(
        "INSERT INTO events (source_id, ts, event_type, name, value, value_kind, created_at)"
        " VALUES (1, ?, 'measurement', 'queue_length', 2, 'gauge', ?)", (now, now))
    fired = alert_engine.evaluate_ongoing(now, {})
    assert fired == []
    rule = db.q1("SELECT condition_state_json FROM alert_rules WHERE name='Queue spike'")
    assert db.jload(rule["condition_state_json"], {}) == {}


def test_webhook_failure_does_not_block_ingestion(client, calibrated_source, monkeypatch):
    """_webhook runs on a daemon thread and swallows all exceptions -- a
    webhook endpoint that's down must never raise into the ingestion path."""
    import urllib.request as urllib_request

    def boom(*args, **kwargs):
        raise ConnectionRefusedError("simulated webhook failure")
    monkeypatch.setattr(urllib_request, "urlopen", boom)

    zone = client.post("/api/v1/zones", json={
        "name": "Checkout",
        "polygon": [{"x": 4, "y": 3}, {"x": 6, "y": 3}, {"x": 6, "y": 5}, {"x": 4, "y": 5}],
    }).json()
    client.post("/api/v1/alert-rules", json={
        "name": "Test", "kind": "event_match", "webhook_url": "http://example.invalid/hook",
        "params": {"event_type": "detection", "zone_id": zone["id"]},
    })
    response = client.post("/api/v1/observations/batch", json={"observations": [
        make_detection(calibrated_source, "wh-1", 1000.0, point_px=(500, 400)),
    ]})
    assert response.status_code == 200
    assert response.json()["accepted"] == 1
