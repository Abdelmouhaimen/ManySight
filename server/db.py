"""SQLite storage layer for StoreLens. Plain sqlite3, WAL mode, dict rows."""
import json
import os
import sqlite3
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.environ.get("STORELENS_DATA", os.path.join(ROOT, "data"))
DB_PATH = os.path.join(DATA_DIR, "storelens.db")
SNAP_DIR = os.path.join(DATA_DIR, "snapshots")

SCHEMA = """
CREATE TABLE IF NOT EXISTS stores (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL DEFAULT 'My space',
  width_m REAL NOT NULL DEFAULT 20,
  height_m REAL NOT NULL DEFAULT 12,
  map_json TEXT NOT NULL DEFAULT '{}',
  environment TEXT NOT NULL DEFAULT 'setup',
  created_at REAL
);
CREATE TABLE IF NOT EXISTS sources (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  store_id INTEGER NOT NULL DEFAULT 1,
  name TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'rtsp',            -- rtsp | webrtc | http | webcam | file
  url TEXT DEFAULT '',
  username TEXT DEFAULT '',
  password TEXT DEFAULT '',
  extra_json TEXT DEFAULT '{}',
  status TEXT DEFAULT 'unknown',                -- unknown | online | offline | unsupported
  last_checked REAL,
  map_x REAL, map_y REAL,
  rotation_deg REAL DEFAULT 0,
  fov_deg REAL DEFAULT 70,
  calibration_json TEXT,                        -- {points, H, error_m, frame_w, frame_h}
  created_at REAL
);
CREATE TABLE IF NOT EXISTS zones (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  store_id INTEGER NOT NULL DEFAULT 1,
  name TEXT NOT NULL,
  ztype TEXT NOT NULL DEFAULT 'area',           -- checkout | entrance | fridge | aisle | area | custom
  color TEXT DEFAULT '',
  polygon_json TEXT NOT NULL,                   -- [{x,y}, ...] in map meters
  created_at REAL
);
CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  description TEXT DEFAULT '',
  source_ids TEXT DEFAULT '[]',
  event_types TEXT DEFAULT '[]',
  status TEXT DEFAULT 'active',                 -- active | paused | done
  created_at REAL,
  last_event_at REAL,
  event_count INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id INTEGER,
  source_id INTEGER,
  ts REAL NOT NULL,
  event_type TEXT NOT NULL DEFAULT 'detection',
  track_id TEXT,
  zone_id INTEGER,
  x_px REAL, y_px REAL,
  x_map REAL, y_map REAL,
  value REAL,
  label TEXT,
  attributes TEXT DEFAULT '{}',
  created_at REAL
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_type_ts ON events(event_type, ts);
CREATE INDEX IF NOT EXISTS idx_events_zone ON events(zone_id, ts);
CREATE INDEX IF NOT EXISTS idx_events_job ON events(job_id, ts);
CREATE INDEX IF NOT EXISTS idx_events_track ON events(track_id, ts);
CREATE TABLE IF NOT EXISTS alert_rules (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  kind TEXT NOT NULL,                           -- dwell_exceeds | occupancy_exceeds | state_alert | event_match
  params_json TEXT NOT NULL DEFAULT '{}',
  webhook_url TEXT DEFAULT '',
  cooldown_s REAL DEFAULT 60,
  enabled INTEGER DEFAULT 1,
  last_fired_at REAL,
  created_at REAL
);
CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  rule_id INTEGER,
  ts REAL NOT NULL,
  title TEXT NOT NULL,
  message TEXT DEFAULT '',
  payload_json TEXT DEFAULT '{}',
  acknowledged INTEGER DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'new',
  note TEXT DEFAULT '',
  resolved_at REAL,
  created_at REAL
);
CREATE TABLE IF NOT EXISTS insight_definitions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  question TEXT DEFAULT '',
  block TEXT NOT NULL,                          -- metric|line|bar|table|heatmap_map|flow_matrix|state_timeline
  dataset TEXT NOT NULL,                        -- summary|heatmap|dwell|occupancy|counts|transitions|states
  params_json TEXT NOT NULL DEFAULT '{}',       -- dataset query params: zone_id, label, group_by, source_id, field...
  unit TEXT DEFAULT '',
  limitations TEXT DEFAULT '',
  pinned INTEGER DEFAULT 0,
  sort_order INTEGER DEFAULT 0,
  visibility TEXT NOT NULL DEFAULT 'visible',   -- visible | hidden
  created_by TEXT NOT NULL DEFAULT 'user',      -- user | agent
  status TEXT NOT NULL DEFAULT 'ready',         -- draft|collecting|validating|ready|degraded|retired
  created_at REAL,
  updated_at REAL
);
"""


def connect() -> sqlite3.Connection:
    os.makedirs(SNAP_DIR, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=15)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    return con


def init_db():
    con = connect()
    try:
        con.executescript(SCHEMA)
        # Lightweight migrations for existing installations. SQLite's
        # CREATE TABLE IF NOT EXISTS does not add newly introduced columns.
        store_columns = {r[1] for r in con.execute("PRAGMA table_info(stores)").fetchall()}
        if "space_type" not in store_columns:
            con.execute("ALTER TABLE stores ADD COLUMN space_type TEXT NOT NULL DEFAULT 'store'")
        if "environment" not in store_columns:
            con.execute("ALTER TABLE stores ADD COLUMN environment TEXT NOT NULL DEFAULT 'setup'")
        alert_columns = {r[1] for r in con.execute("PRAGMA table_info(alerts)").fetchall()}
        if "status" not in alert_columns:
            con.execute("ALTER TABLE alerts ADD COLUMN status TEXT NOT NULL DEFAULT 'new'")
        if "note" not in alert_columns:
            con.execute("ALTER TABLE alerts ADD COLUMN note TEXT DEFAULT ''")
        if "resolved_at" not in alert_columns:
            con.execute("ALTER TABLE alerts ADD COLUMN resolved_at REAL")
        con.execute("UPDATE alerts SET status='resolved' WHERE acknowledged=1 AND status='new'")
        row = con.execute("SELECT id FROM stores WHERE id=1").fetchone()
        if not row:
            con.execute(
                "INSERT INTO stores (id, name, width_m, height_m, map_json, created_at) VALUES (1,'My space',20,12,'{}',?)",
                (time.time(),),
            )
        con.commit()
    finally:
        con.close()


def q(sql: str, args=()) -> list[dict]:
    con = connect()
    try:
        return [dict(r) for r in con.execute(sql, args).fetchall()]
    finally:
        con.close()


def q1(sql: str, args=()) -> dict | None:
    rows = q(sql, args)
    return rows[0] if rows else None


def ex(sql: str, args=()) -> int:
    con = connect()
    try:
        cur = con.execute(sql, args)
        con.commit()
        return cur.lastrowid
    finally:
        con.close()


def exmany(sql: str, seq) -> int:
    con = connect()
    try:
        cur = con.executemany(sql, seq)
        con.commit()
        return cur.rowcount
    finally:
        con.close()


def jload(s, default=None):
    if s is None or s == "":
        return default if default is not None else {}
    try:
        return json.loads(s)
    except (ValueError, TypeError):
        return default if default is not None else {}


def now() -> float:
    return time.time()
