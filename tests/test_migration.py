"""Schema migration: fresh init, insight-to-analysis migration, rerun safety
(server/db.py:_migrate_insights_to_analyses)."""
import json


def test_fresh_database_initializes_without_error(isolated_db):
    row = isolated_db.q1("SELECT id FROM stores WHERE id=1")
    assert row is not None


def test_events_table_has_v2_columns(isolated_db):
    columns = {r[1] for r in isolated_db.connect().execute("PRAGMA table_info(events)").fetchall()}
    for expected in ("schema_version", "observation_id", "worker_id", "name", "entity_type",
                     "value_kind", "unit", "confidence", "identity_scope", "identity_model_version"):
        assert expected in columns


def test_analyses_table_exists(isolated_db):
    columns = {r[1] for r in isolated_db.connect().execute("PRAGMA table_info(analyses)").fetchall()}
    assert "subject" in columns
    assert "measures_json" in columns


def test_legacy_insight_is_migrated_to_analysis(isolated_db):
    now = isolated_db.now()
    isolated_db.ex(
        "INSERT INTO insight_definitions (title, question, block, dataset, params_json, unit,"
        " limitations, pinned, sort_order, visibility, created_by, status, created_at, updated_at)"
        " VALUES ('Dwell by zone','How long?','bar','dwell','{}','sec','',0,0,'visible','user','ready',?,?)",
        (now, now),
    )
    isolated_db.init_db()  # migration runs inside init_db(); call again to trigger it post-insert
    migrated = isolated_db.q1(
        "SELECT * FROM analyses WHERE migrated_from_insight_id IS NOT NULL AND subject='detection'"
    )
    assert migrated is not None
    assert json.loads(migrated["measures_json"]) == ["visits", "average_dwell", "total_dwell"]


def test_migration_is_idempotent_on_rerun(isolated_db):
    now = isolated_db.now()
    insight_id = isolated_db.ex(
        "INSERT INTO insight_definitions (title, question, block, dataset, params_json, unit,"
        " limitations, pinned, sort_order, visibility, created_by, status, created_at, updated_at)"
        " VALUES ('States','How long open?','state_timeline','states','{}','',0,0,0,'visible','user','ready',?,?)",
        (now, now),
    )
    isolated_db.init_db()
    isolated_db.init_db()
    isolated_db.init_db()
    count = isolated_db.q1(
        "SELECT COUNT(*) n FROM analyses WHERE migrated_from_insight_id=?", (insight_id,)
    )["n"]
    assert count == 1  # not duplicated across repeated init_db() calls


def test_deleting_migrated_analysis_does_not_resurrect_it(isolated_db):
    now = isolated_db.now()
    insight_id = isolated_db.ex(
        "INSERT INTO insight_definitions (title, question, block, dataset, params_json, unit,"
        " limitations, pinned, sort_order, visibility, created_by, status, created_at, updated_at)"
        " VALUES ('Old chart','Legacy chart','line','occupancy','{}','',0,0,0,'visible','user','ready',?,?)",
        (now, now),
    )
    isolated_db.init_db()
    migrated = isolated_db.q1(
        "SELECT id FROM analyses WHERE migrated_from_insight_id=?", (insight_id,)
    )
    assert migrated is not None

    isolated_db.ex("DELETE FROM analyses WHERE id=?", (migrated["id"],))
    isolated_db.init_db()
    isolated_db.init_db()

    assert isolated_db.q1(
        "SELECT id FROM analyses WHERE migrated_from_insight_id=?", (insight_id,)
    ) is None
    assert isolated_db.q1(
        "SELECT insight_id FROM legacy_insight_migrations WHERE insight_id=?", (insight_id,)
    ) is not None


def test_unrepresentable_legacy_dataset_is_marked_not_dropped(isolated_db):
    now = isolated_db.now()
    isolated_db.ex(
        "INSERT INTO insight_definitions (title, question, block, dataset, params_json, unit,"
        " limitations, pinned, sort_order, visibility, created_by, status, created_at, updated_at)"
        " VALUES ('Mystery','','metric','unknown_future_dataset','{}','',0,0,0,'visible','user','ready',?,?)",
        (now, now),
    )
    isolated_db.init_db()
    migrated = isolated_db.q1(
        "SELECT * FROM analyses WHERE migrated_from_insight_id IS NOT NULL AND migration_note LIKE '%unrecognized%'"
    )
    assert migrated is not None  # present, not silently dropped
    assert migrated["migration_note"]


def test_no_data_reset_existing_rows_survive_init(isolated_db):
    isolated_db.ex("INSERT INTO sources (name, kind, created_at) VALUES ('cam','webcam',0)")
    isolated_db.init_db()
    isolated_db.init_db()
    count = isolated_db.q1("SELECT COUNT(*) n FROM sources")["n"]
    assert count == 1


def test_legacy_webcam_locator_is_promoted_but_external_ref_is_preserved(uninitialized_db):
    con = uninitialized_db.connect()
    con.execute(
        "CREATE TABLE sources (id INTEGER PRIMARY KEY, name TEXT, kind TEXT, url TEXT, "
        "locator_json TEXT DEFAULT '{}', capabilities_json TEXT DEFAULT '[]', created_at REAL)"
    )
    con.execute(
        "INSERT INTO sources VALUES (1,'Legacy webcam','webcam','0','{}','[]',0)"
    )
    con.execute(
        "INSERT INTO sources VALUES (2,'External camera','http','',?, '[]',0)",
        (json.dumps({"local_secret_ref": "CAMERA_URL"}),),
    )
    con.commit()
    con.close()

    uninitialized_db.init_db()
    webcam = uninitialized_db.q1("SELECT * FROM sources WHERE id=1")
    external = uninitialized_db.q1("SELECT * FROM sources WHERE id=2")
    assert webcam["connection_management"] == "manysight_managed"
    assert json.loads(webcam["connection_config_json"]) == {"device_index": 0}
    assert external["connection_management"] == "external_secret"
    assert json.loads(external["locator_json"]) == {"local_secret_ref": "CAMERA_URL"}


def test_existing_events_table_adds_sample_column_before_index(uninitialized_db):
    con = uninitialized_db.connect()
    con.execute(
        "CREATE TABLE events (id INTEGER PRIMARY KEY, job_id INTEGER, source_id INTEGER, "
        "event_type TEXT, ts REAL, track_id TEXT, label TEXT, x REAL, y REAL, bbox TEXT, "
        "zone_id INTEGER, projection_surface_id INTEGER, zone_view_id INTEGER, "
        "zone_assignment_method TEXT, projection_method TEXT, zone_revision INTEGER, "
        "calibration_revision INTEGER, surface_revision INTEGER, zone_view_revision INTEGER, "
        "attributes TEXT DEFAULT '{}', created_at REAL)"
    )
    con.execute(
        "INSERT INTO events (id,source_id,event_type,ts,track_id,created_at) "
        "VALUES (1,1,'detection',1000,'legacy-track',1000)"
    )
    con.commit(); con.close()

    uninitialized_db.init_db()
    columns = {row[1] for row in uninitialized_db.connect().execute(
        "PRAGMA table_info(events)").fetchall()}
    assert {"sample_id", "space_revision_id"}.issubset(columns)
    assert uninitialized_db.q1("SELECT track_id FROM events WHERE id=1")["track_id"] == "legacy-track"
