"""SQLite storage layer for ManySight. Plain sqlite3, WAL mode, dict rows."""
import contextlib
import hashlib
import json
import os
import sqlite3
import threading
import time
from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.environ.get("MANYSIGHT_DATA", os.path.join(ROOT, "data"))
DB_PATH = os.path.join(DATA_DIR, "manysight.db")
_DB_PATH_OVERRIDE: ContextVar[str | None] = ContextVar("manysight_db_path", default=None)
_DB_CONNECTION_OVERRIDE: ContextVar[sqlite3.Connection | None] = ContextVar(
    "manysight_db_connection", default=None,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS stores (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL DEFAULT 'My space',
  width_m REAL NOT NULL DEFAULT 20,
  height_m REAL NOT NULL DEFAULT 12,
  map_json TEXT NOT NULL DEFAULT '{}',
  environment TEXT NOT NULL DEFAULT 'setup',
  current_space_revision_id INTEGER NOT NULL DEFAULT 1,
  created_at REAL
);
CREATE TABLE IF NOT EXISTS space_revisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  store_id INTEGER NOT NULL DEFAULT 1,
  revision_number INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'current',
  reason TEXT NOT NULL DEFAULT 'initial',
  snapshot_json TEXT NOT NULL DEFAULT '{}',
  created_at REAL NOT NULL,
  UNIQUE(store_id, revision_number)
);
CREATE TABLE IF NOT EXISTS sources (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  store_id INTEGER NOT NULL DEFAULT 1,
  name TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'rtsp',            -- rtsp | webrtc | http | webcam | file
  connection_mode TEXT NOT NULL DEFAULT 'agent_local', -- agent_local | edge_gateway
  connection_management TEXT NOT NULL DEFAULT 'external_secret', -- external_secret | manysight_managed
  connection_config_json TEXT NOT NULL DEFAULT '{}',   -- safe, structured connection fields
  connection_revision INTEGER NOT NULL DEFAULT 0,
  locator_json TEXT NOT NULL DEFAULT '{}',      -- non-secret local device / secret reference
  capabilities_json TEXT NOT NULL DEFAULT '[]', -- video | audio | detections | custom
  metadata_json TEXT NOT NULL DEFAULT '{}',     -- non-secret agent/domain metadata
  last_observation_at REAL,                     -- timestamp reported by a worker
  last_ingestion_at REAL,                       -- time ManySight last received an observation
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
  polygon_json TEXT NOT NULL,                   -- legacy first exterior ring in map meters
  geometry_json TEXT,                           -- canonical GeoJSON Polygon | MultiPolygon
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
  sample_id TEXT,
  space_revision_id INTEGER NOT NULL DEFAULT 1,
  created_at REAL
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_type_ts ON events(event_type, ts);
CREATE INDEX IF NOT EXISTS idx_events_zone ON events(zone_id, ts);
CREATE INDEX IF NOT EXISTS idx_events_job ON events(job_id, ts);
CREATE INDEX IF NOT EXISTS idx_events_track ON events(track_id, ts);
CREATE TABLE IF NOT EXISTS source_current_samples (
  source_id INTEGER NOT NULL,
  entity_type TEXT NOT NULL,
  sample_id TEXT,
  sample_key TEXT NOT NULL,
  ts REAL NOT NULL,
  expected_count INTEGER NOT NULL,
  marker_event_id INTEGER NOT NULL,
  marker_observation_id TEXT,
  completed_at REAL NOT NULL,
  PRIMARY KEY(source_id, entity_type)
);
CREATE TABLE IF NOT EXISTS source_current_entities (
  source_id INTEGER NOT NULL,
  entity_type TEXT NOT NULL,
  sample_key TEXT NOT NULL,
  event_id INTEGER NOT NULL,
  local_entity_id TEXT,
  worker_id INTEGER,
  x_map REAL,
  y_map REAL,
  zone_id INTEGER,
  confidence REAL,
  ts REAL NOT NULL,
  PRIMARY KEY(source_id, entity_type, sample_key, event_id)
);
CREATE INDEX IF NOT EXISTS idx_source_current_entities_sample
  ON source_current_entities(source_id, entity_type, sample_key);
CREATE TABLE IF NOT EXISTS zone_geometry_provenance (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  zone_id INTEGER NOT NULL,
  source_id INTEGER,
  source_calibration_revision INTEGER,
  zone_view_id INTEGER,
  zone_view_revision INTEGER,
  projection_surface_id INTEGER,
  projection_surface_revision INTEGER,
  original_pixel_polygon_json TEXT,
  projected_map_polygon_json TEXT NOT NULL,
  operation TEXT NOT NULL,
  resulting_zone_revision INTEGER NOT NULL,
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_zone_geometry_provenance_zone
  ON zone_geometry_provenance(zone_id, resulting_zone_revision);
CREATE TABLE IF NOT EXISTS camera_calibrations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_id INTEGER NOT NULL UNIQUE,
  provider TEXT NOT NULL DEFAULT 'generic',
  original_projection_matrix_json TEXT,
  projection_matrix_json TEXT NOT NULL,
  world_to_map_transform_json TEXT NOT NULL DEFAULT '[[1,0,0],[0,1,0],[0,0,1]]',
  distortion_json TEXT NOT NULL DEFAULT '[]',
  intrinsics_json TEXT NOT NULL DEFAULT '{}',
  extrinsics_json TEXT NOT NULL DEFAULT '{}',
  world_frame_json TEXT NOT NULL DEFAULT '{}',
  ground_plane_z REAL NOT NULL DEFAULT 0,
  units TEXT NOT NULL,
  frame_w INTEGER,
  frame_h INTEGER,
  derived_homography_json TEXT NOT NULL,
  verification_json TEXT NOT NULL DEFAULT '{}',
  revision INTEGER NOT NULL DEFAULT 1,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS multiview_groups (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  source_ids_json TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  algorithm TEXT NOT NULL DEFAULT 'geometry_tracklet',
  algorithm_version TEXT NOT NULL DEFAULT '1',
  configuration_revision INTEGER NOT NULL DEFAULT 1,
  time_tolerance_s REAL NOT NULL DEFAULT 0.75,
  spatial_gate_m REAL NOT NULL DEFAULT 1.5,
  track_age_s REAL NOT NULL DEFAULT 2.0,
  topology_json TEXT NOT NULL DEFAULT '{}',
  configuration_json TEXT NOT NULL DEFAULT '{}',
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS fused_entities (
  id TEXT PRIMARY KEY,
  group_id INTEGER NOT NULL,
  entity_type TEXT NOT NULL,
  algorithm TEXT NOT NULL,
  algorithm_version TEXT NOT NULL,
  configuration_revision INTEGER NOT NULL,
  space_revision_id INTEGER NOT NULL DEFAULT 1,
  created_at REAL NOT NULL,
  last_seen_at REAL NOT NULL,
  ended_at REAL
);
CREATE INDEX IF NOT EXISTS idx_fused_entities_group_active
  ON fused_entities(group_id, entity_type, ended_at, last_seen_at);
CREATE TABLE IF NOT EXISTS fused_entity_members (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  fused_entity_id TEXT NOT NULL,
  source_id INTEGER NOT NULL,
  worker_id INTEGER,
  local_entity_id TEXT NOT NULL,
  sample_key TEXT NOT NULL,
  source_event_id INTEGER,
  joined_at REAL NOT NULL,
  last_seen_at REAL NOT NULL,
  association_cost REAL,
  UNIQUE(fused_entity_id, source_id, worker_id, local_entity_id)
);
CREATE INDEX IF NOT EXISTS idx_fused_members_local
  ON fused_entity_members(source_id, worker_id, local_entity_id, last_seen_at);
CREATE TABLE IF NOT EXISTS fused_observations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  fused_entity_id TEXT NOT NULL,
  group_id INTEGER NOT NULL,
  ts REAL NOT NULL,
  x_map REAL NOT NULL,
  y_map REAL NOT NULL,
  zone_id INTEGER,
  confidence REAL,
  quality TEXT NOT NULL DEFAULT 'known',
  member_evidence_json TEXT NOT NULL,
  algorithm TEXT NOT NULL,
  algorithm_version TEXT NOT NULL,
  configuration_revision INTEGER NOT NULL,
  space_revision_id INTEGER NOT NULL DEFAULT 1,
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fused_observations_entity_ts
  ON fused_observations(fused_entity_id, ts);
CREATE TABLE IF NOT EXISTS fused_current_entities (
  fused_entity_id TEXT PRIMARY KEY,
  group_id INTEGER NOT NULL,
  entity_type TEXT NOT NULL,
  ts REAL NOT NULL,
  x_map REAL NOT NULL,
  y_map REAL NOT NULL,
  zone_id INTEGER,
  confidence REAL,
  quality TEXT NOT NULL,
  member_evidence_json TEXT NOT NULL,
  updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fused_current_zone
  ON fused_current_entities(group_id, entity_type, zone_id);
CREATE TABLE IF NOT EXISTS zone_current_occupancy (
  group_id INTEGER NOT NULL,
  zone_id INTEGER NOT NULL,
  entity_type TEXT NOT NULL,
  value INTEGER NOT NULL,
  quality TEXT NOT NULL,
  as_of REAL NOT NULL,
  provenance_json TEXT NOT NULL DEFAULT '{}',
  PRIMARY KEY(group_id, zone_id, entity_type)
);
CREATE TABLE IF NOT EXISTS zone_occupancy_observations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  group_id INTEGER NOT NULL,
  zone_id INTEGER NOT NULL,
  entity_type TEXT NOT NULL,
  ts REAL NOT NULL,
  value INTEGER NOT NULL,
  quality TEXT NOT NULL,
  provenance_json TEXT NOT NULL DEFAULT '{}',
  space_revision_id INTEGER NOT NULL DEFAULT 1,
  created_at REAL NOT NULL,
  UNIQUE(group_id, zone_id, entity_type, ts)
);
CREATE INDEX IF NOT EXISTS idx_zone_occupancy_history
  ON zone_occupancy_observations(group_id, zone_id, entity_type, ts);
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
  space_revision_id INTEGER NOT NULL DEFAULT 1,
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
-- insight_definitions above is deprecated: superseded by `analyses` (unified
-- saved-analysis model) below. Kept for historical rows and best-effort
-- migration; the API no longer writes new rows to insight_definitions.
CREATE TABLE IF NOT EXISTS analyses (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  question TEXT DEFAULT '',
  subject TEXT NOT NULL,                        -- detection | measurement | state
  measures_json TEXT NOT NULL DEFAULT '[]',     -- ["active_entities", ...]
  filters_json TEXT NOT NULL DEFAULT '{}',
  grouping_json TEXT NOT NULL DEFAULT '{}',     -- {primary, bucket, split_by}
  default_range_json TEXT NOT NULL DEFAULT '{}',
  comparison_json TEXT NOT NULL DEFAULT '{}',
  presentation TEXT DEFAULT '',                 -- optional preferred renderer hint
  pinned INTEGER DEFAULT 0,
  sort_order INTEGER DEFAULT 0,
  visibility TEXT NOT NULL DEFAULT 'visible',   -- visible | hidden
  created_by TEXT NOT NULL DEFAULT 'user',      -- user | agent
  status TEXT NOT NULL DEFAULT 'ready',
  query_hash TEXT,                              -- normalized (subject,measures,filters,grouping) hash; duplicate detection
  migrated_from_insight_id INTEGER,
  migration_note TEXT DEFAULT '',
  created_at REAL,
  updated_at REAL
);
CREATE TABLE IF NOT EXISTS source_credentials (
  source_id INTEGER PRIMARY KEY,
  encrypted_payload TEXT NOT NULL,
  credential_type TEXT NOT NULL DEFAULT 'source_connection',
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_analyses_query_hash ON analyses(query_hash);
-- Durable receipt for the one-time legacy insight migration.  This lives
-- separately from `analyses` so deleting a migrated analysis does not make it
-- appear to be "not migrated" and resurrect it on the next server startup.
CREATE TABLE IF NOT EXISTS legacy_insight_migrations (
  insight_id INTEGER PRIMARY KEY,
  migrated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS dashboards (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  created_by TEXT NOT NULL DEFAULT 'agent',
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS dashboard_widgets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  dashboard_id INTEGER NOT NULL,
  query_id INTEGER NOT NULL,
  title TEXT NOT NULL,
  presentation TEXT NOT NULL,
  configuration_json TEXT NOT NULL DEFAULT '{}',
  sort_order INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dashboard_widgets_dashboard
  ON dashboard_widgets(dashboard_id, sort_order, id);
CREATE TABLE IF NOT EXISTS demo_sessions (
  id TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  recipe_version TEXT NOT NULL,
  mode TEXT NOT NULL DEFAULT 'guided',
  workspace_path TEXT NOT NULL,
  asset_root TEXT,
  playback_epoch INTEGER NOT NULL DEFAULT 0,
  playback_position_s REAL NOT NULL DEFAULT 0,
  playback_started_at REAL,
  duration_s REAL NOT NULL DEFAULT 0,
  retained_epochs INTEGER NOT NULL DEFAULT 2,
  action_log_json TEXT NOT NULL DEFAULT '[]',
  result_json TEXT NOT NULL DEFAULT '{}',
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  expires_at REAL
);
"""


def current_db_path() -> str:
    """Return the request/task-local database, or the normal workspace database."""
    return _DB_PATH_OVERRIDE.get() or DB_PATH


@contextmanager
def using_database(path: str):
    """Temporarily route all db helpers to one isolated SQLite workspace.

    Context variables are async-task local, so concurrent demo requests cannot
    leak rows into the normal workspace or into another demo session.
    """
    token = _DB_PATH_OVERRIDE.set(os.path.abspath(path))
    try:
        yield
    finally:
        _DB_PATH_OVERRIDE.reset(token)


# Durability of a commit, in WAL mode.
#
#   NORMAL (default) — the commit is written to the WAL before the request
#     returns, but not fsync'd. The database is never corrupted, and accepted
#     evidence survives a crash of this process; a host power loss or kernel
#     panic can lose the most recent transactions.
#   FULL — fsync on every commit, so a committed sample survives power loss.
#     Measured cost on ordinary hardware is roughly 5 ms per commit, i.e. a
#     ceiling near 200 commits/second for the whole workspace. A 4 x 60 FPS
#     deployment submits 240 samples/second and cannot be served under it.
#
# Set MANYSIGHT_SQLITE_SYNCHRONOUS=FULL where power-loss durability of the last
# few frames matters more than keeping up with the cameras.
SYNCHRONOUS = os.environ.get("MANYSIGHT_SQLITE_SYNCHRONOUS", "NORMAL").upper()
if SYNCHRONOUS not in {"OFF", "NORMAL", "FULL", "EXTRA"}:
    SYNCHRONOUS = "NORMAL"


def connect(path: str | None = None) -> sqlite3.Connection:
    """Open a new dedicated connection. The caller owns and closes it."""
    resolved = os.path.abspath(path or current_db_path())
    os.makedirs(os.path.dirname(resolved), exist_ok=True)
    con = sqlite3.connect(resolved, timeout=15)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute(f"PRAGMA synchronous={SYNCHRONOUS}")
    con.execute("PRAGMA foreign_keys=ON")
    return con


# Closing the last connection to a WAL database runs a checkpoint and removes
# the -wal file. The helpers below used to open and close a connection per call,
# so every read and every write paid for a checkpoint — measured at ~4.7 ms,
# which alone put a ceiling of a couple of hundred operations per second on the
# whole pipeline. They now borrow a long-lived connection instead.
#
# The pool is thread-local: sqlite3 connections are not safe to share between
# threads, and FastAPI runs sync endpoints and `asyncio.to_thread` work on a
# thread pool, so a handful of connections per workspace is all that exists.
# A small per-thread bound keeps a long test session or a series of demo
# workspaces from accumulating file handles.
POOL_SIZE_PER_THREAD = 4
_pool = threading.local()


def pooled_connection(path: str | None = None) -> sqlite3.Connection:
    """The calling thread's long-lived connection for one database path."""
    resolved = os.path.abspath(path or current_db_path())
    connections = getattr(_pool, "connections", None)
    if connections is None:
        connections = _pool.connections = OrderedDict()
    con = connections.get(resolved)
    if con is None:
        con = connect(resolved)
        connections[resolved] = con
        while len(connections) > POOL_SIZE_PER_THREAD:
            _, evicted = connections.popitem(last=False)
            with contextlib.suppress(sqlite3.Error):
                evicted.close()
    else:
        connections.move_to_end(resolved)
    return con


def close_pooled_connections() -> None:
    """Release this thread's pooled connections (shutdown and tests)."""
    connections = getattr(_pool, "connections", None)
    if not connections:
        return
    for con in connections.values():
        with contextlib.suppress(sqlite3.Error):
            con.close()
    connections.clear()


# SQLite allows one writer at a time. Left to itself, a contended writer is
# parked by the busy handler, which sleeps in growing steps (1, 2, 5, 10 ... ms)
# and so pays far more than the lock was actually held for. Four camera threads
# plus a fusion tick hit that constantly. Since one process owns a workspace
# (see services/realtime.py), an in-process mutex per database orders writers
# fairly and hands the lock over the moment it is free. The busy timeout stays
# as the backstop for the rare writer outside this path.
_write_lock_registry: dict[str, threading.RLock] = {}
_write_lock_registry_guard = threading.Lock()


def write_lock(path: str | None = None) -> threading.RLock:
    resolved = os.path.abspath(path or current_db_path())
    lock = _write_lock_registry.get(resolved)
    if lock is None:
        with _write_lock_registry_guard:
            lock = _write_lock_registry.setdefault(resolved, threading.RLock())
    return lock


@contextmanager
def transaction():
    """Reuse one SQLite connection and commit once across related db helpers."""
    existing = _DB_CONNECTION_OVERRIDE.get()
    if existing is not None:
        yield existing
        return
    con = pooled_connection()
    lock = write_lock()
    lock.acquire()
    token = _DB_CONNECTION_OVERRIDE.set(con)
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        _DB_CONNECTION_OVERRIDE.reset(token)
        lock.release()


def init_db(path: str | None = None):
    con = connect(path)
    try:
        con.executescript(SCHEMA)
        # Lightweight migrations for existing installations. SQLite's
        # CREATE TABLE IF NOT EXISTS does not add newly introduced columns.
        store_columns = {r[1] for r in con.execute("PRAGMA table_info(stores)").fetchall()}
        if "space_type" not in store_columns:
            con.execute("ALTER TABLE stores ADD COLUMN space_type TEXT NOT NULL DEFAULT 'store'")
        if "environment" not in store_columns:
            con.execute("ALTER TABLE stores ADD COLUMN environment TEXT NOT NULL DEFAULT 'setup'")
        if "current_space_revision_id" not in store_columns:
            con.execute("ALTER TABLE stores ADD COLUMN current_space_revision_id INTEGER NOT NULL DEFAULT 1")
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
            "connection_management": "TEXT NOT NULL DEFAULT 'external_secret'",
            "connection_config_json": "TEXT NOT NULL DEFAULT '{}'",
            "connection_revision": "INTEGER NOT NULL DEFAULT 0",
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
        # `connection_management` has exactly two values: a source either keeps
        # its connection here or points at a secret the worker machine holds.
        # A pre-release database may carry the managed value under its previous
        # spelling, so normalize by the invariant rather than by that spelling:
        # anything that is not external_secret is the managed mode.
        con.execute(
            "UPDATE sources SET connection_management='manysight_managed' "
            "WHERE connection_management NOT IN ('external_secret', 'manysight_managed')"
        )
        # Safe webcam indices were historically stored in locator_json. Promote
        # them to the managed connection model without touching external refs.
        con.execute(
            "UPDATE sources SET connection_management='manysight_managed', "
            "connection_config_json=locator_json, connection_revision=1 "
            "WHERE kind='webcam' AND connection_management='external_secret' "
            "AND json_extract(locator_json, '$.device_index') IS NOT NULL"
        )
        # Dormant legacy columns are not part of the managed credential model.
        # Scrub them after safe webcam migration so old plaintext never survives;
        # new secrets belong only in authenticated ciphertext in source_credentials.
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
        if "geometry_json" not in zone_columns:
            con.execute("ALTER TABLE zones ADD COLUMN geometry_json TEXT")
        for zone in con.execute(
            "SELECT id, polygon_json FROM zones WHERE geometry_json IS NULL OR geometry_json=''"
        ).fetchall():
            polygon = jload(zone["polygon_json"], [])
            ring = [[float(p["x"]), float(p["y"])] for p in polygon]
            if ring and ring[0] != ring[-1]:
                ring.append(ring[0])
            con.execute(
                "UPDATE zones SET geometry_json=? WHERE id=?",
                (json.dumps({"type": "Polygon", "coordinates": [ring]}), zone["id"]),
            )
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
            # Observation contract v2 (schema_version=2): workers submit only
            # detection/measurement/state kinds (stored in the existing event_type
            # column) plus these columns. `track_id` doubles as the opaque
            # `entity_id`; `label` and `attributes` carry per-kind meaning documented
            # in docs/adr/0001-observation-contract.md. Legacy rows keep schema_version=1
            # and their original event_type (zone_enter/zone_exit/zone_dwell/
            # state_change/count/transition/custom) for historical audit.
            "schema_version": "INTEGER NOT NULL DEFAULT 1",
            "observation_id": "TEXT",
            "worker_id": "INTEGER",
            "name": "TEXT",
            "entity_type": "TEXT",
            "value_kind": "TEXT",
            "unit": "TEXT",
            "confidence": "REAL",
            "identity_scope": "TEXT",
            "identity_model_version": "TEXT",
            "sample_id": "TEXT",
            "space_revision_id": "INTEGER NOT NULL DEFAULT 1",
        }
        for column, sql_type in event_migrations.items():
            if column not in event_columns:
                con.execute(f"ALTER TABLE events ADD COLUMN {column} {sql_type}")
        con.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_events_observation_id "
            "ON events(observation_id) WHERE observation_id IS NOT NULL"
        )
        con.execute("CREATE INDEX IF NOT EXISTS idx_events_entity ON events(track_id, name, ts)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_events_source_name ON events(source_id, name, ts)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_events_worker ON events(worker_id, ts)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_events_sample ON events(source_id, sample_id, ts)")
        alert_columns = {r[1] for r in con.execute("PRAGMA table_info(alerts)").fetchall()}
        if "status" not in alert_columns:
            con.execute("ALTER TABLE alerts ADD COLUMN status TEXT NOT NULL DEFAULT 'new'")
        if "note" not in alert_columns:
            con.execute("ALTER TABLE alerts ADD COLUMN note TEXT DEFAULT ''")
        if "resolved_at" not in alert_columns:
            con.execute("ALTER TABLE alerts ADD COLUMN resolved_at REAL")
        if "space_revision_id" not in alert_columns:
            con.execute("ALTER TABLE alerts ADD COLUMN space_revision_id INTEGER NOT NULL DEFAULT 1")
        for table in ("fused_entities", "fused_observations", "zone_occupancy_observations"):
            columns = {r[1] for r in con.execute(f"PRAGMA table_info({table})").fetchall()}
            if "space_revision_id" not in columns:
                con.execute(f"ALTER TABLE {table} ADD COLUMN space_revision_id INTEGER NOT NULL DEFAULT 1")
        con.execute("UPDATE alerts SET status='resolved' WHERE acknowledged=1 AND status='new'")
        rule_columns = {r[1] for r in con.execute("PRAGMA table_info(alert_rules)").fetchall()}
        if "analysis_json" not in rule_columns:
            con.execute("ALTER TABLE alert_rules ADD COLUMN analysis_json TEXT")
        if "condition_json" not in rule_columns:
            con.execute("ALTER TABLE alert_rules ADD COLUMN condition_json TEXT")
        if "condition_state_json" not in rule_columns:
            con.execute("ALTER TABLE alert_rules ADD COLUMN condition_state_json TEXT DEFAULT '{}'")
        calibration_columns = {
            r[1] for r in con.execute("PRAGMA table_info(camera_calibrations)").fetchall()
        }
        if "original_projection_matrix_json" not in calibration_columns:
            con.execute("ALTER TABLE camera_calibrations ADD COLUMN original_projection_matrix_json TEXT")
            con.execute(
                "UPDATE camera_calibrations SET original_projection_matrix_json=projection_matrix_json "
                "WHERE original_projection_matrix_json IS NULL"
            )
        if "world_to_map_transform_json" not in calibration_columns:
            con.execute(
                "ALTER TABLE camera_calibrations ADD COLUMN world_to_map_transform_json TEXT "
                "NOT NULL DEFAULT '[[1,0,0],[0,1,0],[0,0,1]]'"
            )
        # _migrate_insights_to_analyses() below indexes every one of these
        # columns directly (row["visibility"], row["created_by"], row["status"],
        # row["pinned"], row["sort_order"]) on every insight_definitions row --
        # an older insight_definitions table missing any of them must be brought
        # up to the current shape (with the same defaults as CREATE TABLE, so
        # existing rows backfill deterministically) before that migration runs,
        # exactly like every other table above.
        insight_columns = {r[1] for r in con.execute("PRAGMA table_info(insight_definitions)").fetchall()}
        insight_migrations = {
            "pinned": "INTEGER DEFAULT 0",
            "sort_order": "INTEGER DEFAULT 0",
            "visibility": "TEXT NOT NULL DEFAULT 'visible'",
            "created_by": "TEXT NOT NULL DEFAULT 'user'",
            "status": "TEXT NOT NULL DEFAULT 'ready'",
        }
        for column, sql_type in insight_migrations.items():
            if column not in insight_columns:
                con.execute(f"ALTER TABLE insight_definitions ADD COLUMN {column} {sql_type}")
        # Count lines now default to the last instantaneous observation in each
        # interval. Update only legacy template text, never user-authored wording.
        legacy_count_limitations = (
            "Averages worker-reported count samples per interval; accuracy depends on the model.",
            "Averages per-frame model counts per interval; accuracy depends on the model.",
            "The curve averages count samples per interval; the headline is the latest raw observation. Accuracy depends on the model.",
            "The curve averages per-frame model counts per interval; the headline is the latest raw observation. Accuracy depends on the model.",
        )
        con.execute(
            f"UPDATE insight_definitions SET limitations=? WHERE dataset='counts'"
            f" AND limitations IN ({','.join('?' for _ in legacy_count_limitations)})",
            (
                "Each curve point and the headline use the last raw count observation in their interval. Accuracy depends on the model.",
                *legacy_count_limitations,
            ),
        )
        row = con.execute("SELECT id FROM stores WHERE id=1").fetchone()
        if not row:
            con.execute(
                "INSERT INTO stores (id, name, width_m, height_m, map_json, created_at) VALUES (1,'My space',20,12,'{}',?)",
                (time.time(),),
            )
        revision = con.execute(
            "SELECT id FROM space_revisions WHERE store_id=1 AND revision_number=1"
        ).fetchone()
        if not revision:
            con.execute(
                "INSERT INTO space_revisions (store_id,revision_number,status,reason,snapshot_json,created_at) "
                "VALUES (1,1,'current','initial','{}',?)",
                (time.time(),),
            )
        _migrate_insights_to_analyses(con)
        con.commit()
    finally:
        con.close()
    # init_db drives a raw connection, so it bypasses the `ex`/`exmany` hook.
    from .services import config_cache
    config_cache.invalidate("init_db")


def current_space_revision_id() -> int:
    """The active space revision, cached until a configuration write occurs.

    Read several times per observation (validation, enrichment, every insert),
    so at 240 samples/second the uncached version was thousands of queries a
    second for a value that only changes when the space is reinitialized — a
    write to `stores`, which invalidates the configuration cache.
    """
    from .services import config_cache
    return config_cache.current_space_revision_id()


def read_current_space_revision_id() -> int:
    row = q1("SELECT current_space_revision_id FROM stores WHERE id=1")
    return int(row["current_space_revision_id"] if row else 1)


def analysis_hash(subject: str, measures: list, filters: dict, grouping: dict) -> str:
    """Canonical hash for duplicate-analysis detection — same (subject, measures,
    filters, grouping) is the same question regardless of presentation."""
    normalized = json.dumps(
        {"subject": subject, "measures": sorted(measures),
         "filters": filters, "grouping": grouping},
        sort_keys=True,
    )
    return hashlib.sha256(normalized.encode()).hexdigest()[:24]


def _map_insight_to_analysis(row: dict) -> dict:
    """Best-effort translation of a legacy block+dataset+params insight into the
    unified analysis shape. Approximate by nature — see migration_note on the
    result and docs/adr/0001-observation-contract.md 'Legacy insight mapping'."""
    params = jload(row["params_json"], {})
    dataset, block = row["dataset"], row["block"]
    subject, measures, filters, grouping, presentation, note = "detection", [], {}, {}, "", ""
    zone_id = params.get("zone_id")
    if zone_id is not None:
        filters["zone_ids"] = [zone_id]
    if dataset == "summary":
        field = params.get("field", "tracks")
        measures = ["active_entities"] if field == "active_tracks" else ["distinct_entities"]
        if field not in {"tracks", "active_tracks"}:
            note = f"legacy summary field '{field}' has no direct analog; approximated as distinct_entities"
    elif dataset == "heatmap":
        subject, measures, presentation = "detection", ["density"], "heatmap_map"
        if params.get("label"):
            filters["labels"] = [params["label"]]
        if params.get("source_id") is not None:
            filters["source_ids"] = [params["source_id"]]
    elif dataset == "dwell":
        subject = "detection"
        measures = ["visits", "average_dwell", "total_dwell"]
        grouping = {"primary": "zone"} if block in {"bar", "table"} else {}
        if params.get("group_by"):
            grouping["split_by"] = [params["group_by"]]
    elif dataset == "occupancy":
        subject, measures = "detection", ["active_entities"]
        grouping = {"primary": "time", "bucket": "5m"}
        if params.get("label"):
            filters["labels"] = [params["label"]]
        if params.get("group_by") == "label":
            grouping["split_by"] = ["label"]
    elif dataset == "counts":
        subject = "measurement"
        measures = ["average"] if params.get("aggregation") == "avg" else ["latest"]
        grouping = {"primary": "time", "bucket": "5m"}
        if params.get("label"):
            filters["measurement_names"] = [params["label"]]
    elif dataset == "transitions":
        subject, measures, grouping, presentation = "detection", ["transition_count"], {"primary": "zone"}, "flow_matrix"
    elif dataset == "states":
        subject, measures, presentation = "state", ["duration"], "state_timeline"
        if params.get("source_id") is not None:
            filters["source_ids"] = [params["source_id"]]
    else:
        note = f"unrecognized legacy dataset '{dataset}' — migrated as an empty detection KPI; review manually"
    return {
        "name": row["title"], "question": row["question"], "subject": subject,
        "measures": measures, "filters": filters, "grouping": grouping,
        "presentation": presentation,
        "note": note or f"auto-migrated from legacy block='{block}' dataset='{dataset}' params={params}",
    }


def _migrate_insights_to_analyses(con: sqlite3.Connection):
    """One-time, idempotent best-effort migration of insight_definitions rows into
    the unified analyses table. Never drops a legacy row; every insight gets a
    corresponding analyses row, even an approximate or unrepresentable one, with
    migration_note explaining the mapping so nothing is silently lost."""
    # Backfill durable receipts for databases upgraded from the original
    # migration implementation, where the analyses row itself was the marker.
    con.execute(
        "INSERT OR IGNORE INTO legacy_insight_migrations (insight_id, migrated_at) "
        "SELECT migrated_from_insight_id, COALESCE(created_at, ?) FROM analyses "
        "WHERE migrated_from_insight_id IS NOT NULL",
        (time.time(),),
    )
    already = {
        r[0]
        for r in con.execute(
            "SELECT insight_id FROM legacy_insight_migrations"
        ).fetchall()
    }
    rows = con.execute("SELECT * FROM insight_definitions").fetchall()
    now = time.time()
    for row in rows:
        row = dict(row)
        if row["id"] in already:
            continue
        mapped = _map_insight_to_analysis(row)
        query_hash = analysis_hash(mapped["subject"], mapped["measures"], mapped["filters"], mapped["grouping"])
        con.execute(
            "INSERT INTO analyses (name, question, subject, measures_json, filters_json, grouping_json,"
            " default_range_json, comparison_json, presentation, pinned, sort_order, visibility,"
            " created_by, status, query_hash, migrated_from_insight_id, migration_note, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (mapped["name"], mapped["question"], mapped["subject"], json.dumps(mapped["measures"]),
             json.dumps(mapped["filters"]), json.dumps(mapped["grouping"]), "{}", "{}",
             mapped["presentation"], row["pinned"], row["sort_order"], row["visibility"],
             row["created_by"], row["status"], query_hash, row["id"], mapped["note"], now, now),
        )
        con.execute(
            "INSERT OR IGNORE INTO legacy_insight_migrations (insight_id, migrated_at) VALUES (?, ?)",
            (row["id"], now),
        )


def active_connection() -> sqlite3.Connection | None:
    """The connection of an enclosing `transaction()`, if one is open.

    Lets a service join the caller's single ingestion transaction instead of
    opening and committing its own.
    """
    return _DB_CONNECTION_OVERRIDE.get()


def q(sql: str, args=()) -> list[dict]:
    con = _DB_CONNECTION_OVERRIDE.get() or pooled_connection()
    return [dict(r) for r in con.execute(sql, args).fetchall()]


def q1(sql: str, args=()) -> dict | None:
    rows = q(sql, args)
    return rows[0] if rows else None


def _note_write(sql: str) -> None:
    """Single choke point for configuration-cache invalidation.

    Every configuration mutation in the application reaches SQLite through `ex`
    or `exmany`, so classifying the statement here means a new mutation endpoint
    cannot forget to invalidate. The two paths that drive a raw connection
    instead (`init_db` and the guided demo's promotion transaction) invalidate
    explicitly. Imported lazily to keep `db` free of service imports.
    """
    from .services import config_cache
    config_cache.note_write(sql)


def ex(sql: str, args=()) -> int:
    con = _DB_CONNECTION_OVERRIDE.get()
    owned = con is None
    if owned:
        con = pooled_connection()
        write_lock().acquire()
    try:
        cur = con.execute(sql, args)
        if owned:
            con.commit()
        return cur.lastrowid
    except Exception:
        if owned:
            # A pooled connection outlives the call; never leave a partial
            # statement for the next caller to commit by accident.
            con.rollback()
        raise
    finally:
        if owned:
            write_lock().release()
        _note_write(sql)


def exmany(sql: str, seq) -> int:
    con = _DB_CONNECTION_OVERRIDE.get()
    owned = con is None
    if owned:
        con = pooled_connection()
        write_lock().acquire()
    try:
        cur = con.executemany(sql, seq)
        if owned:
            con.commit()
        return cur.rowcount
    except Exception:
        if owned:
            con.rollback()
        raise
    finally:
        if owned:
            write_lock().release()
        _note_write(sql)


def jload(s, default=None):
    if s is None or s == "":
        return default if default is not None else {}
    try:
        return json.loads(s)
    except (ValueError, TypeError):
        return default if default is not None else {}


def now() -> float:
    return time.time()
