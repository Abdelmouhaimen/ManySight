"""Workspace-scoped cache of configuration that ingestion re-read on every batch.

Zones, calibrations, projection surfaces, zone views and multiview groups are
*configuration*, not per-frame state: at 4x60 FPS the old code re-selected all of
them 240 times a second and rebuilt the same Shapely geometry for every detection
x zone containment test.  This module keeps one prepared, read-only copy per
workspace database and per space revision.

Correctness is based on configuration writes, never on a TTL.  Every helper in
``server/db.py`` that executes a statement funnels through ``note_write()``; a
write touching a configuration table bumps a process-wide generation and the next
reader rebuilds.  ``note_write`` classifies each SQL string once and memoizes the
answer, so the hot ingestion path pays a dict lookup, not a scan.

Two write paths do not go through ``db.ex``/``db.exmany`` — ``db.init_db()`` and
the guided demo's promotion transaction, which both drive a raw connection.  They
call ``invalidate()`` explicitly; ``tests/test_config_cache.py`` covers every
mutation route through the public API.

Execution model: this cache, like the realtime coordinator, assumes one process
owns a workspace database.  See ``docs/realtime-pipeline.md``.
"""
from __future__ import annotations

import threading
from collections import OrderedDict

from .. import db
from . import zone_geometry

# Tables whose contents change how an observation is enriched, which group a
# source belongs to, or which alert rules exist.
CONFIG_TABLES = (
    "sources", "zones", "projection_surfaces", "zone_views", "multiview_groups",
    "camera_calibrations", "alert_rules", "stores",
)
# Columns on configuration tables that carry runtime state rather than
# configuration. An UPDATE touching only these cannot change what is cached, and
# they are written on hot paths — per ingested batch (`sources` counters) and per
# fired alert (`alert_rules` cooldown and condition state) — so treating them as
# configuration would defeat the cache entirely.
RUNTIME_COLUMNS = frozenset({
    "event_count", "last_ingestion_at", "last_observation_at",
    "last_fired_at", "condition_state_json",
})
WRITE_VERBS = ("insert", "update", "delete", "replace", "alter", "drop", "create")

# Demo sessions and tests each use their own database file; a handful of live
# workspaces is plenty and keeps this bounded.
MAX_WORKSPACES = 16

_lock = threading.Lock()
_generation = 0
_geometry: "OrderedDict[tuple[str, int], tuple[int, tuple]]" = OrderedDict()
_groups: "OrderedDict[str, tuple[int, dict]]" = OrderedDict()
_alert_rules: "OrderedDict[str, tuple[int, int]]" = OrderedDict()
_space_revision: "OrderedDict[str, tuple[int, int]]" = OrderedDict()
_classified: dict[str, bool] = {}


def generation() -> int:
    with _lock:
        return _generation


def invalidate(_reason: str = "") -> int:
    """Drop every cached configuration view. Cheap: readers rebuild lazily."""
    global _generation
    with _lock:
        _generation += 1
        _geometry.clear()
        _groups.clear()
        _alert_rules.clear()
        _space_revision.clear()
        return _generation


def updated_columns(lowered: str) -> set[str]:
    """Column names in the SET clause of a top-level UPDATE, or an empty set.

    Only used to recognize runtime-column updates, so a shape this cannot parse
    simply falls through to "invalidate" — the conservative answer.
    """
    if not lowered.startswith("update ") or " set " not in lowered:
        return set()
    clause = lowered.split(" set ", 1)[1]
    if " where " in clause:
        clause = clause.split(" where ", 1)[0]
    columns = set()
    for assignment in clause.split(","):
        name, separator, _value = assignment.partition("=")
        if not separator:
            return set()
        columns.add(name.strip())
    return columns


def touches_configuration(sql: str) -> bool:
    """True when this statement can change cached configuration."""
    cached = _classified.get(sql)
    if cached is not None:
        return cached
    lowered = " ".join(sql.lower().split())
    result = False
    if any(verb in lowered for verb in WRITE_VERBS) \
            and any(table in lowered for table in CONFIG_TABLES):
        columns = updated_columns(lowered)
        result = not (columns and columns <= RUNTIME_COLUMNS)
    # SQL strings are module-level constants or f-strings built from a small
    # fixed set of shapes; the classification map cannot grow without bound in
    # normal use, but guard anyway.
    if len(_classified) < 4096:
        _classified[sql] = result
    return result


def note_write(sql: str) -> None:
    if touches_configuration(sql):
        invalidate(sql)


def current_space_revision_id() -> int:
    """Cached backing for `db.current_space_revision_id()`."""
    key = db.current_db_path()
    with _lock:
        entry = _space_revision.get(key)
        current = _generation
    if entry is not None and entry[0] == current:
        return entry[1]
    value = db.read_current_space_revision_id()
    with _lock:
        if _generation == current:
            _remember(_space_revision, key, (current, value))
    return value


def _remember(store: OrderedDict, key, value) -> None:
    store[key] = value
    store.move_to_end(key)
    while len(store) > MAX_WORKSPACES:
        store.popitem(last=False)


# --------------------------------------------------------------------------
# Geometry context
# --------------------------------------------------------------------------

def geometry_context() -> tuple:
    """The tuple ``enrich.enrich_one`` consumes, built at most once per change.

    Shape is unchanged from the original ``enrich.load_geometry_context()``:
    ``(zones, calibrations, surfaces, views_by_source, views_by_id, zone_by_name)``.
    Each zone additionally carries a ``prepared`` containment test.  Callers must
    treat every structure as read-only — it is shared by concurrent requests.
    """
    key = (db.current_db_path(), db.current_space_revision_id())
    with _lock:
        entry = _geometry.get(key)
        current = _generation
    if entry is not None and entry[0] == current:
        return entry[1]
    context = _build_geometry_context()
    with _lock:
        # A concurrent configuration write during the rebuild bumps the
        # generation; store under the generation the build actually observed so
        # the next reader rebuilds rather than trusting a stale snapshot.
        if _generation == current:
            _remember(_geometry, key, (current, context))
    return context


def _build_geometry_context() -> tuple:
    zones = []
    for z in db.q("SELECT id, name, polygon_json, geometry_json, revision FROM zones ORDER BY id"):
        geometry = db.jload(z.get("geometry_json"), None)
        if not geometry:
            geometry = zone_geometry.as_geojson(
                zone_geometry.polygon_from_points(db.jload(z["polygon_json"], [])))
        zones.append({"id": z["id"], "name": z["name"], "revision": z["revision"],
                      "polygon": db.jload(z["polygon_json"], []), "geometry": geometry,
                      "prepared": zone_geometry.prepare(geometry)})
    cals = {}
    for s in db.q("SELECT id, calibration_json, calibration_revision FROM sources"):
        cal = db.jload(s["calibration_json"], None)
        if cal and cal.get("H"):
            cals[s["id"]] = {"H": cal["H"], "revision": s["calibration_revision"]}
    surfaces = {r["id"]: {**r, "H": db.jload(r["homography_json"], None)}
                for r in db.q("SELECT * FROM projection_surfaces")}
    views_by_source, views_by_id = {}, {}
    for r in db.q("SELECT * FROM zone_views ORDER BY id"):
        view = {**r, "outer": db.jload(r["outer_polygon_json"], []),
                "detection": db.jload(r["detection_polygon_json"], [])}
        views_by_source.setdefault(r["source_id"], []).append(view)
        views_by_id[r["id"]] = view
    zone_by_name = {z["name"].lower(): z["id"] for z in zones}
    return zones, cals, surfaces, views_by_source, views_by_id, zone_by_name


def zone_names() -> dict[int, str]:
    return {zone["id"]: zone["name"] for zone in geometry_context()[0]}


def zone_by_id() -> dict[int, dict]:
    return {zone["id"]: zone for zone in geometry_context()[0]}


# --------------------------------------------------------------------------
# Multiview group configuration
# --------------------------------------------------------------------------

def group_config() -> dict:
    """Enabled multiview groups with their JSON columns already decoded.

    Returns ``{"by_id": {group_id: group}, "by_source": {source_id: [group,...]}}``.
    Each group is the raw row plus decoded ``source_ids``, ``topology`` and
    ``neighbors``, so a completed source frame can find its groups without
    selecting and re-decoding every group row.
    """
    key = db.current_db_path()
    with _lock:
        entry = _groups.get(key)
        current = _generation
    if entry is not None and entry[0] == current:
        return entry[1]
    by_id, by_source = {}, {}
    for row in db.q("SELECT * FROM multiview_groups WHERE enabled=1 ORDER BY id"):
        topology = db.jload(row.get("topology_json"), {})
        group = {
            **row,
            "source_ids": db.jload(row["source_ids_json"], []),
            "topology": topology,
            "neighbors": topology.get("neighbors") or {},
        }
        by_id[row["id"]] = group
        for source_id in group["source_ids"]:
            by_source.setdefault(source_id, []).append(group)
    config = {"by_id": by_id, "by_source": by_source}
    with _lock:
        if _generation == current:
            _remember(_groups, key, (current, config))
    return config


def groups_for_source(source_id: int) -> list[dict]:
    return group_config()["by_source"].get(source_id, [])


# --------------------------------------------------------------------------
# Alert rules
# --------------------------------------------------------------------------

def enabled_alert_rule_count() -> int:
    """How many alert rules are enabled, cached until a rule is changed.

    Only the count is cached. Rule rows themselves carry `last_fired_at` and
    `condition_state_json`, which the engine writes as it fires — caching those
    would break cooldown, so the engine still reads rule rows fresh. What this
    removes is the full rule scan on every ingested sample in the common case of
    a workspace with no alert rules at all.
    """
    key = db.current_db_path()
    with _lock:
        entry = _alert_rules.get(key)
        current = _generation
    if entry is not None and entry[0] == current:
        return entry[1]
    row = db.q1("SELECT COUNT(*) n FROM alert_rules WHERE enabled=1")
    count = int(row["n"]) if row else 0
    with _lock:
        if _generation == current:
            _remember(_alert_rules, key, (current, count))
    return count
