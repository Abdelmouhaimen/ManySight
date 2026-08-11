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
