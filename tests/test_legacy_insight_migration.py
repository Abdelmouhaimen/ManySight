"""Additive migration of legacy `insight_definitions` tables that predate the
`pinned`/`sort_order`/`visibility`/`created_by`/`status` columns.

_migrate_insights_to_analyses() indexes every one of these columns directly
(row["visibility"], etc.) on every insight_definitions row, so a table missing
any of them must be upgraded -- with the same ALTER TABLE ... ADD COLUMN
pattern used for every other table in init_db() -- before that migration runs.
These tests hand-build a raw insight_definitions table with a specific column
subset (simulating a historical/foreign schema variant) and let db.init_db()
create every other table fresh, then assert the migration completes without
error, preserves rows, and stays idempotent.
"""
import sqlite3

import pytest

CORE_INSIGHT_COLUMNS = """
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  question TEXT DEFAULT '',
  block TEXT NOT NULL,
  dataset TEXT NOT NULL,
  params_json TEXT NOT NULL DEFAULT '{}',
  unit TEXT DEFAULT '',
  limitations TEXT DEFAULT '',
  created_at REAL,
  updated_at REAL
"""

# The five columns _migrate_insights_to_analyses() reads directly via row[...],
# each with the same type/default the current CREATE TABLE declares.
ALL_MIGRATED_COLUMNS = {
    "pinned": "pinned INTEGER DEFAULT 0",
    "sort_order": "sort_order INTEGER DEFAULT 0",
    "visibility": "visibility TEXT NOT NULL DEFAULT 'visible'",
    "created_by": "created_by TEXT NOT NULL DEFAULT 'user'",
    "status": "status TEXT NOT NULL DEFAULT 'ready'",
}


def _build_legacy_insight_table(db_path, present=()):
    """Create a raw insight_definitions table with only `present` (a subset of
    ALL_MIGRATED_COLUMNS keys) of the migration-critical columns -- simulating
    a table that predates the others."""
    cols = CORE_INSIGHT_COLUMNS.strip()
    for key in present:
        cols += ",\n  " + ALL_MIGRATED_COLUMNS[key]
    con = sqlite3.connect(db_path)
    con.executescript(f"CREATE TABLE insight_definitions (\n{cols}\n);")
    con.commit()
    con.close()


def _insert_legacy_insight(db_path, columns_and_values, table="insight_definitions"):
    con = sqlite3.connect(db_path)
    cols = ", ".join(columns_and_values.keys())
    placeholders = ", ".join("?" for _ in columns_and_values)
    cur = con.execute(f"INSERT INTO {table} ({cols}) VALUES ({placeholders})", tuple(columns_and_values.values()))
    con.commit()
    rowid = cur.lastrowid
    con.close()
    return rowid


@pytest.mark.parametrize("missing", ["visibility", "created_by", "status"])
def test_migration_survives_single_missing_column(uninitialized_db, missing):
    present = [k for k in ALL_MIGRATED_COLUMNS if k != missing]
    _build_legacy_insight_table(uninitialized_db.DB_PATH, present=present)
    _insert_legacy_insight(uninitialized_db.DB_PATH, {
        "title": "Legacy dwell", "block": "bar", "dataset": "dwell", "params_json": "{}",
        **{k: (1 if k in ("pinned", "sort_order") else "visible" if k == "visibility"
              else "user" if k == "created_by" else "ready") for k in present},
    })
    uninitialized_db.init_db()  # must not raise KeyError
    analyses = uninitialized_db.q("SELECT * FROM analyses")
    assert len(analyses) == 1
    assert analyses[0]["migrated_from_insight_id"] == 1
    # the missing column now exists with the CREATE TABLE default, backfilled
    columns = {r[1] for r in sqlite3.connect(uninitialized_db.DB_PATH)
              .execute("PRAGMA table_info(insight_definitions)").fetchall()}
    assert missing in columns


def test_migration_survives_all_three_columns_missing(uninitialized_db):
    _build_legacy_insight_table(uninitialized_db.DB_PATH, present=("pinned", "sort_order"))
    _insert_legacy_insight(uninitialized_db.DB_PATH, {
        "title": "Legacy occupancy", "block": "line", "dataset": "occupancy", "params_json": "{}",
        "pinned": 0, "sort_order": 0,
    })
    uninitialized_db.init_db()
    analyses = uninitialized_db.q("SELECT * FROM analyses")
    assert len(analyses) == 1
    row = sqlite3.connect(uninitialized_db.DB_PATH).execute(
        "SELECT visibility, created_by, status FROM insight_definitions WHERE id=1").fetchone()
    assert row == ("visible", "user", "ready")


def test_migration_survives_every_migrated_column_missing(uninitialized_db):
    """The exact KeyError repro: a table with none of the five columns at all."""
    _build_legacy_insight_table(uninitialized_db.DB_PATH, present=())
    _insert_legacy_insight(uninitialized_db.DB_PATH, {
        "title": "Bare legacy insight", "block": "metric", "dataset": "summary", "params_json": "{}",
    })
    uninitialized_db.init_db()
    analyses = uninitialized_db.q("SELECT * FROM analyses")
    assert len(analyses) == 1
    assert analyses[0]["name"] == "Bare legacy insight"


def test_table_already_current_migrates_normally(uninitialized_db):
    _build_legacy_insight_table(uninitialized_db.DB_PATH, present=tuple(ALL_MIGRATED_COLUMNS))
    _insert_legacy_insight(uninitialized_db.DB_PATH, {
        "title": "Current-shape insight", "block": "table", "dataset": "states", "params_json": "{}",
        "pinned": 1, "sort_order": 2, "visibility": "visible", "created_by": "agent", "status": "ready",
    })
    uninitialized_db.init_db()
    analyses = uninitialized_db.q("SELECT * FROM analyses")
    assert len(analyses) == 1
    assert analyses[0]["name"] == "Current-shape insight"


def test_partially_migrated_database(uninitialized_db):
    """visibility/created_by already added by some prior partial run; pinned/
    sort_order/status not yet -- a combination distinct from the single-column
    and all-missing cases above."""
    _build_legacy_insight_table(uninitialized_db.DB_PATH, present=("visibility", "created_by"))
    _insert_legacy_insight(uninitialized_db.DB_PATH, {
        "title": "Half-upgraded insight", "block": "bar", "dataset": "transitions", "params_json": "{}",
        "visibility": "visible", "created_by": "user",
    })
    uninitialized_db.init_db()
    columns = {r[1] for r in sqlite3.connect(uninitialized_db.DB_PATH)
              .execute("PRAGMA table_info(insight_definitions)").fetchall()}
    assert {"pinned", "sort_order", "status"} <= columns
    analyses = uninitialized_db.q("SELECT * FROM analyses")
    assert len(analyses) == 1


def test_repeated_init_db_is_idempotent(uninitialized_db):
    _build_legacy_insight_table(uninitialized_db.DB_PATH, present=())
    _insert_legacy_insight(uninitialized_db.DB_PATH, {
        "title": "Repeat-safe insight", "block": "line", "dataset": "counts", "params_json": "{}",
    })
    uninitialized_db.init_db()
    uninitialized_db.init_db()
    uninitialized_db.init_db()
    analyses = uninitialized_db.q("SELECT * FROM analyses")
    assert len(analyses) == 1  # never duplicated across three startups


def test_existing_migrated_analyses_rows_are_preserved_not_duplicated(uninitialized_db):
    """A database where insight id=1 was already migrated in a previous
    startup (analyses row with migrated_from_insight_id=1 already present)
    must not be re-migrated; a second, never-migrated insight id=2 must still
    get exactly one new row."""
    _build_legacy_insight_table(uninitialized_db.DB_PATH, present=tuple(ALL_MIGRATED_COLUMNS))
    _insert_legacy_insight(uninitialized_db.DB_PATH, {
        "title": "Already migrated", "block": "bar", "dataset": "dwell", "params_json": "{}",
        "pinned": 0, "sort_order": 0, "visibility": "visible", "created_by": "user", "status": "ready",
    })
    _insert_legacy_insight(uninitialized_db.DB_PATH, {
        "title": "Not yet migrated", "block": "line", "dataset": "occupancy", "params_json": "{}",
        "pinned": 0, "sort_order": 0, "visibility": "visible", "created_by": "user", "status": "ready",
    })
    uninitialized_db.init_db()  # brings the `analyses` table into existence

    # Simulate insight id=1 already having a migrated analyses row from a
    # previous startup, with custom fields a fresh mapping would not reproduce.
    con = sqlite3.connect(uninitialized_db.DB_PATH)
    con.execute(
        "UPDATE analyses SET name=?, migration_note=? WHERE migrated_from_insight_id=1",
        ("Hand-edited name after migration", "custom note preserved"),
    )
    con.commit()
    con.close()

    uninitialized_db.init_db()
    uninitialized_db.init_db()

    analyses = uninitialized_db.q("SELECT * FROM analyses ORDER BY migrated_from_insight_id")
    assert len(analyses) == 2  # still exactly one row per insight, none duplicated
    assert analyses[0]["name"] == "Hand-edited name after migration"
    assert analyses[0]["migration_note"] == "custom note preserved"
    assert analyses[1]["migrated_from_insight_id"] == 2


def test_empty_legacy_table(uninitialized_db):
    _build_legacy_insight_table(uninitialized_db.DB_PATH, present=())
    uninitialized_db.init_db()  # zero rows to migrate -- must not error
    assert uninitialized_db.q("SELECT * FROM analyses") == []


def test_mixed_null_and_default_values(uninitialized_db):
    """Legacy rows with explicit NULLs in nullable fields (not just omitted
    columns) must not crash the mapping -- e.g. jload(None) and params.get(...)
    on an empty dict."""
    _build_legacy_insight_table(uninitialized_db.DB_PATH, present=tuple(ALL_MIGRATED_COLUMNS))
    _insert_legacy_insight(uninitialized_db.DB_PATH, {
        # params_json is NOT NULL (omitted here to take its '{}' table default;
        # SQLite rejects an *explicit* NULL against a NOT NULL column even when
        # a DEFAULT exists) -- the other nullable fields are explicitly NULL.
        "title": "Null params insight", "block": "metric", "dataset": "summary",
        "question": None, "unit": None, "limitations": None,
        "pinned": None, "sort_order": None, "visibility": "visible", "created_by": "user", "status": "ready",
    })
    uninitialized_db.init_db()
    analyses = uninitialized_db.q("SELECT * FROM analyses")
    assert len(analyses) == 1
    assert analyses[0]["filters_json"] == "{}"
