"""Detection-derived visits/dwell (services/derive.py:derive_visits_from_detections),
merged with legacy zone_enter/zone_exit derivation (derive_visits)."""
from server.services import derive


def _insert_detection(db, zone_id, source_id, ts, entity_id="e1"):
    db.ex(
        "INSERT INTO events (source_id, ts, event_type, track_id, zone_id, attributes, created_at)"
        " VALUES (?,?,?,?,?,?,?)",
        (source_id, ts, "detection", entity_id, zone_id, "{}", ts),
    )


def test_visit_requires_minimum_confirmed_samples(isolated_db):
    zone = isolated_db.ex("INSERT INTO zones (name, ztype, polygon_json, created_at, updated_at)"
                          " VALUES ('Z','area','[]',0,0)")
    _insert_detection(isolated_db, zone, 1, 1000.0)  # only one sample -- boundary noise
    visits, _ = derive.derive_visits_from_detections(900, 1100, min_samples=2)
    assert visits == []


def test_visit_confirmed_with_enough_samples(isolated_db):
    zone = isolated_db.ex("INSERT INTO zones (name, ztype, polygon_json, created_at, updated_at)"
                          " VALUES ('Z','area','[]',0,0)")
    for ts in (1000.0, 1010.0, 1020.0):
        _insert_detection(isolated_db, zone, 1, ts)
    visits, open_count = derive.derive_visits_from_detections(900, 1100, min_samples=2, gap_s=45)
    assert len(visits) == 1
    assert visits[0]["value"] == 20.0  # 1020 - 1000
    # last sample (1020) is within gap_s of `until` (1100? no -- until=1100, gap=45,
    # 1100-1020=80 > 45) -- so this should read as completed, not open.
    assert visits[0]["completed"] is True
    assert open_count == 0


def test_gap_splits_into_two_visits(isolated_db):
    zone = isolated_db.ex("INSERT INTO zones (name, ztype, polygon_json, created_at, updated_at)"
                          " VALUES ('Z','area','[]',0,0)")
    for ts in (1000.0, 1010.0):
        _insert_detection(isolated_db, zone, 1, ts)
    for ts in (1200.0, 1210.0):  # gap of 190s >> default 45s gap tolerance
        _insert_detection(isolated_db, zone, 1, ts)
    visits, _ = derive.derive_visits_from_detections(900, 1300, min_samples=2)
    assert len(visits) == 2


def test_ongoing_visit_reported_open(isolated_db):
    zone = isolated_db.ex("INSERT INTO zones (name, ztype, polygon_json, created_at, updated_at)"
                          " VALUES ('Z','area','[]',0,0)")
    now = 1100.0
    for ts in (1000.0, 1050.0, now - 5):  # last sample 5s before `until`, well within gap_s
        _insert_detection(isolated_db, zone, 1, ts)
    visits, open_count = derive.derive_visits_from_detections(900, now, min_samples=2, gap_s=45)
    assert open_count == 1
    assert visits[0]["completed"] is False
    assert visits[0]["value"] == now - 1000.0  # clipped to `until`, not the last sample


def test_merges_legacy_and_current_contract_visits(isolated_db):
    zone = isolated_db.ex("INSERT INTO zones (name, ztype, polygon_json, created_at, updated_at)"
                          " VALUES ('Z','area','[]',0,0)")
    # legacy zone_enter/zone_exit pair
    isolated_db.ex("INSERT INTO events (source_id,ts,event_type,track_id,zone_id,attributes,created_at)"
                   " VALUES (1,1000,'zone_enter','legacy1',?,'{}',1000)", (zone,))
    isolated_db.ex("INSERT INTO events (source_id,ts,event_type,track_id,zone_id,attributes,created_at)"
                   " VALUES (1,1060,'zone_exit','legacy1',?,'{}',1060)", (zone,))
    # current-contract detections for a different entity
    for ts in (1000.0, 1010.0, 1020.0):
        _insert_detection(isolated_db, zone, 1, ts, entity_id="current1")
    visits, _ = derive.derive_visits(900, 1200, zone_id=None)
    entities = {v["track_id"] for v in visits}
    assert entities == {"legacy1", "current1"}


def test_min_confirm_samples_does_not_drop_legacy_pairs(isolated_db):
    """The min-samples confirmation rule is specific to the detection-derived
    path; a single legacy zone_enter/zone_exit pair must still count."""
    zone = isolated_db.ex("INSERT INTO zones (name, ztype, polygon_json, created_at, updated_at)"
                          " VALUES ('Z','area','[]',0,0)")
    isolated_db.ex("INSERT INTO events (source_id,ts,event_type,track_id,zone_id,attributes,created_at)"
                   " VALUES (1,1000,'zone_enter','legacy1',?,'{}',1000)", (zone,))
    isolated_db.ex("INSERT INTO events (source_id,ts,event_type,track_id,zone_id,attributes,created_at)"
                   " VALUES (1,1060,'zone_exit','legacy1',?,'{}',1060)", (zone,))
    visits, _ = derive.derive_visits(900, 1200, zone_id=None)
    assert len(visits) == 1
    assert visits[0]["value"] == 60.0
