"""SQLite storage layer for StoreLens. Plain sqlite3, WAL mode, dict rows."""
import json
import os
import sqlite3
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.environ.get("STORELENS_DATA", os.path.join(ROOT, "data"))
DB_PATH = os.path.join(DATA_DIR, "storelens.db")

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
  connection_mode TEXT NOT NULL DEFAULT 'agent_local', -- agent_local | edge_gateway
  locator_json TEXT NOT NULL DEFAULT '{}',      -- non-secret local device / secret reference
  capabilities_json TEXT NOT NULL DEFAULT '[]', -- video | audio | detections | custom
  metadata_json TEXT NOT NULL DEFAULT '{}',     -- non-secret agent/domain metadata
  last_observation_at REAL,                     -- timestamp reported by a worker
  last_ingestion_at REAL,                       -- time StoreLens last received an observation
  event_count INTEGER NOT NULL DEFAULT 0,
  map_x REAL, map_y REAL,
  rotation_deg REAL DEFAULT 0,
  fov_deg REAL DEFAULT 70,
  calibration_json TEXT,                        -- {points, H, error_m, frame_w, frame_h}
  calibration_revision INTEGER NOT NULL DEFAULT 0,
  created_at REAL
);
CREATE TABLE IF NOT EXISTS zones (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  store_id INTEGER NOT NULL DEFAULT 1,
  name TEXT NOT NULL,
  ztype TEXT NOT NULL DEFAULT 'area',           -- checkout | entrance | fridge | aisle | area | custom
  color TEXT DEFAULT '',
  polygon_json TEXT NOT NULL,                   -- [{x,y}, ...] in map meters
  revision INTEGER NOT NULL DEFAULT 1,
  created_at REAL,
  updated_at REAL
);
CREATE TABLE IF NOT EXISTS projection_surfaces (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_id INTEGER NOT NULL,
  name TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'custom',          -- mattress | table | shelf | conveyor | custom
  height_m REAL,
  points_json TEXT NOT NULL DEFAULT '[]',       -- [{px:{x,y}, map:{x,y}}, ...]
  homography_json TEXT NOT NULL,                -- 3x3 pixel -> map matrix
  error_m REAL,
  frame_w INTEGER,
  frame_h INTEGER,
  revision INTEGER NOT NULL DEFAULT 1,
  created_at REAL,
  updated_at REAL,
  UNIQUE(source_id, name)
);
CREATE TABLE IF NOT EXISTS zone_views (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  zone_id INTEGER NOT NULL,
  source_id INTEGER NOT NULL,
  outer_polygon_json TEXT NOT NULL,             -- visible boundary in source pixels
  detection_polygon_json TEXT NOT NULL,         -- inset/decision ROI in source pixels
  projection_surface_id INTEGER,
  membership_rule TEXT NOT NULL DEFAULT 'point', -- point | bbox_overlap | keypoints_inside
  threshold REAL NOT NULL DEFAULT 0.5,
  min_keypoints INTEGER NOT NULL DEFAULT 1,
  revision INTEGER NOT NULL DEFAULT 1,
  created_at REAL,
  updated_at REAL,
  UNIQUE(zone_id, source_id)
);
CREATE INDEX IF NOT EXISTS idx_projection_surfaces_source ON projection_surfaces(source_id);
CREATE INDEX IF NOT EXISTS idx_zone_views_source ON zone_views(source_id);
CREATE INDEX IF NOT EXISTS idx_zone_views_zone ON zone_views(zone_id);
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
  bbox_json TEXT,
  keypoints_json TEXT,
  mask_json TEXT,
  point_kind TEXT,
  projection_surface_id INTEGER,
  zone_view_id INTEGER,
  zone_assignment_method TEXT,
  projection_method TEXT,
  zone_revision INTEGER,
  calibration_revision INTEGER,
  surface_revision INTEGER,
  zone_view_revision INTEGER,
  attributes TEXT DEFAULT '{}',
  created_at REAL
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_type_ts ON events(event_type, ts);
CREATE INDEX IF NOT EXISTS idx_events_zone ON events(zone_id, ts);
CREATE INDEX IF NOT EXISTS idx_events_job ON events(job_id, ts);
CREATE INDEX IF NOT EXISTS idx_events_track ON events(track_id, ts);
CREATE TABLE IF NOT EXISTS worker_instances (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  worker_id TEXT NOT NULL UNIQUE,
  job_id INTEGER NOT NULL,
  name TEXT NOT NULL DEFAULT '',
  version TEXT DEFAULT '',
  config_json TEXT NOT NULL DEFAULT '{}',
  metrics_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'starting',      -- starting | running | stopping | stopped | error
  desired_state TEXT NOT NULL DEFAULT 'running', -- running | stopped | restart
  started_at REAL,
  last_heartbeat_at REAL,
  stopped_at REAL,
  last_error TEXT DEFAULT '',
  created_at REAL,
  updated_at REAL
);
CREATE INDEX IF NOT EXISTS idx_workers_job ON worker_instances(job_id, created_at);
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
    os.makedirs(DATA_DIR, exist_ok=True)
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
        source_columns = {r[1] for r in con.execute("PRAGMA table_info(sources)").fetchall()}
        if "calibration_revision" not in source_columns:
            con.execute("ALTER TABLE sources ADD COLUMN calibration_revision INTEGER NOT NULL DEFAULT 0")
        source_migrations = {
            "connection_mode": "TEXT NOT NULL DEFAULT 'agent_local'",
            "locator_json": "TEXT NOT NULL DEFAULT '{}'",
            "capabilities_json": "TEXT NOT NULL DEFAULT '[]'",
            "metadata_json": "TEXT NOT NULL DEFAULT '{}'",
            "last_observation_at": "REAL",
            "last_ingestion_at": "REAL",
            "event_count": "INTEGER NOT NULL DEFAULT 0",
        }
        for column, sql_type in source_migrations.items():
            if column not in source_columns:
                con.execute(f"ALTER TABLE sources ADD COLUMN {column} {sql_type}")
        legacy_url = "url" if "url" in source_columns else "'' AS url"
        for source in con.execute(
            f"SELECT id, kind, {legacy_url}, locator_json, capabilities_json FROM sources"
        ).fetchall():
            locator = jload(source["locator_json"], {})
            capabilities = jload(source["capabilities_json"], [])
            changed = False
            if not capabilities and source["kind"] in {"rtsp", "webrtc", "http", "webcam", "file"}:
                capabilities = ["video"]
                changed = True
            if not locator and source["kind"] == "webcam" and str(source["url"] or "").isdigit():
                locator = {"device_index": int(source["url"])}
                changed = True
            if changed:
                con.execute(
                    "UPDATE sources SET locator_json=?, capabilities_json=? WHERE id=?",
                    (json.dumps(locator), json.dumps(capabilities), source["id"]),
                )
        # The online architecture never retains camera connection material. Once
        # safe webcam indices have been migrated, scrub dormant legacy fields so
        # upgrading an old database does not leave credentials behind.
        scrub_values = {
            "url": "''",
            "username": "''",
            "password": "''",
            "extra_json": "'{}'",
        }
        scrub = [f"{column}={value}" for column, value in scrub_values.items() if column in source_columns]
        if scrub:
            con.execute(f"UPDATE sources SET {', '.join(scrub)}")
        con.execute(
            "UPDATE sources SET "
            "event_count=(SELECT COUNT(*) FROM events WHERE events.source_id=sources.id), "
            "last_observation_at=(SELECT MAX(ts) FROM events WHERE events.source_id=sources.id), "
            "last_ingestion_at=(SELECT MAX(created_at) FROM events WHERE events.source_id=sources.id) "
            "WHERE last_ingestion_at IS NULL AND EXISTS "
            "(SELECT 1 FROM events WHERE events.source_id=sources.id)"
        )
        zone_columns = {r[1] for r in con.execute("PRAGMA table_info(zones)").fetchall()}
        if "revision" not in zone_columns:
            con.execute("ALTER TABLE zones ADD COLUMN revision INTEGER NOT NULL DEFAULT 1")
        if "updated_at" not in zone_columns:
            con.execute("ALTER TABLE zones ADD COLUMN updated_at REAL")
        event_columns = {r[1] for r in con.execute("PRAGMA table_info(events)").fetchall()}
        event_migrations = {
            "bbox_json": "TEXT",
            "keypoints_json": "TEXT",
            "mask_json": "TEXT",
            "point_kind": "TEXT",
            "projection_surface_id": "INTEGER",
            "zone_view_id": "INTEGER",
            "zone_assignment_method": "TEXT",
            "projection_method": "TEXT",
            "zone_revision": "INTEGER",
            "calibration_revision": "INTEGER",
            "surface_revision": "INTEGER",
            "zone_view_revision": "INTEGER",
        }
        for column, sql_type in event_migrations.items():
            if column not in event_columns:
                con.execute(f"ALTER TABLE events ADD COLUMN {column} {sql_type}")
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
