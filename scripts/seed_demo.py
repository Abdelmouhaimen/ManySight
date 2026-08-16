"""Seed ManySight with a complete, realistic demo using the current observation
contract: store plan, zones, cameras (with a real computed homography), jobs,
~3 hours of synthetic history as `detection`/`measurement`/`state` observations
only (never zone_enter/zone_exit/state_change/count — the platform derives
visits, dwell, transitions, and state intervals from these rows itself), alert
rules (including one unified analysis_condition rule), fired alerts, and a
saved-analysis catalogue.

Run once (server may be running or not — writes the same SQLite DB):
    python scripts/seed_demo.py
Then open http://localhost:8000 — every tab is populated.

Warning: this development utility replaces the workspace map, zones, sources,
alert rules, historical alerts, and non-migrated saved analyses in MANYSIGHT_DATA.
Use a disposable database.
"""
import json
import os
import random
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import db  # noqa: E402
from server.services import homography  # noqa: E402

random.seed(42)
NOW = time.time()
HISTORY_S = 3 * 3600


def clear_previous_seed():
    for job in db.q("SELECT id FROM jobs WHERE name LIKE 'Seed:%'"):
        db.ex("DELETE FROM events WHERE job_id=?", (job["id"],))
        db.ex("DELETE FROM worker_instances WHERE job_id=?", (job["id"],))
        db.ex("DELETE FROM jobs WHERE id=?", (job["id"],))


def seed_store():
    walls = [
        [{"x": 0, "y": 0}, {"x": 20, "y": 0}, {"x": 20, "y": 12}, {"x": 0, "y": 12}, {"x": 0, "y": 0}],
        [{"x": 5.1, "y": 1.5}, {"x": 5.1, "y": 8.5}],
        [{"x": 9.3, "y": 1.5}, {"x": 9.3, "y": 8.5}],
        [{"x": 11.5, "y": 9.5}, {"x": 15.5, "y": 9.5}],
        [{"x": 2.5, "y": 0.3}, {"x": 2.5, "y": 3.2}],
    ]
    labels = [
        {"x": 2.0, "y": 10.5, "text": "Stockroom"},
        {"x": 1.4, "y": 4.6, "text": "Dairy"},
        {"x": 13.5, "y": 8.8, "text": "Self-checkout"},
    ]
    db.ex("UPDATE stores SET name=?, space_type='store', environment='demo', width_m=?, height_m=?, map_json=? WHERE id=1",
          ("Demo Minimart", 20, 12, json.dumps({"walls": walls, "labels": labels})))
    print("store: Demo Minimart 20x12m")


ZONES = [
    ("Entrance", "entrance", "#3987e5", [(16.5, 9.8), (19.7, 9.8), (19.7, 11.7), (16.5, 11.7)]),
    ("Checkout", "checkout", "#199e70", [(11.5, 9.5), (15.5, 9.5), (15.5, 11.5), (11.5, 11.5)]),
    ("Fridge", "fridge", "#c98500", [(0.3, 0.3), (2.5, 0.3), (2.5, 3.2), (0.3, 3.2)]),
    ("Aisle A", "aisle", "#008300", [(4.0, 1.5), (6.2, 1.5), (6.2, 8.5), (4.0, 8.5)]),
    ("Aisle B", "aisle", "#9085e9", [(8.2, 1.5), (10.4, 1.5), (10.4, 8.5), (8.2, 8.5)]),
    ("Promo corner", "area", "#e66767", [(13.0, 1.0), (17.0, 1.0), (17.0, 4.0), (13.0, 4.0)]),
]


def seed_zones() -> dict[str, dict]:
    db.ex("DELETE FROM zone_views")
    db.ex("DELETE FROM zones")
    out = {}
    for name, ztype, color, poly in ZONES:
        polygon = [{"x": x, "y": y} for x, y in poly]
        zid = db.ex("INSERT INTO zones (name, ztype, color, polygon_json, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                    (name, ztype, color, json.dumps(polygon), NOW, NOW))
        out[name] = {"id": zid, "name": name, "ztype": ztype, "polygon": polygon}
    print(f"zones: {list(out)}")
    return out


def seed_sources() -> dict[str, int]:
    db.ex("DELETE FROM zone_views")
    db.ex("DELETE FROM projection_surfaces")
    db.ex("DELETE FROM sources")
    ids = {}
    # 1 — entrance cam with a real homography computed from 5 point pairs
    pairs = [
        {"px": {"x": 140, "y": 690}, "map": {"x": 16.3, "y": 11.6}},
        {"px": {"x": 1150, "y": 700}, "map": {"x": 19.6, "y": 11.4}},
        {"px": {"x": 420, "y": 260}, "map": {"x": 15.2, "y": 8.4}},
        {"px": {"x": 1000, "y": 300}, "map": {"x": 19.0, "y": 8.8}},
        {"px": {"x": 700, "y": 480}, "map": {"x": 17.6, "y": 10.2}},
    ]
    H, err = homography.compute_homography(pairs)
    cal = {"points": pairs, "H": H, "error_m": err, "frame_w": 1280, "frame_h": 720}
    ids["entrance"] = db.ex(
        "INSERT INTO sources (name, kind, connection_mode, locator_json, capabilities_json, metadata_json, "
        "map_x, map_y, rotation_deg, fov_deg, calibration_json, calibration_revision, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("Entrance cam", "rtsp", "agent_local", json.dumps({"local_secret_ref": "entrance_camera"}),
         json.dumps(["video"]), json.dumps({"purpose": "entrance traffic"}),
         18.6, 11.2, 211, 85, json.dumps(cal), 1, NOW))
    # 2 — overhead webcam with a clean top-down homography
    pairs2 = [
        {"px": {"x": 100, "y": 100}, "map": {"x": 6.0, "y": 2.0}},
        {"px": {"x": 1180, "y": 100}, "map": {"x": 14.0, "y": 2.0}},
        {"px": {"x": 1180, "y": 620}, "map": {"x": 14.0, "y": 10.0}},
        {"px": {"x": 100, "y": 620}, "map": {"x": 6.0, "y": 10.0}},
    ]
    H2, err2 = homography.compute_homography(pairs2)
    cal2 = {"points": pairs2, "H": H2, "error_m": err2, "frame_w": 1280, "frame_h": 720}
    ids["overhead"] = db.ex(
        "INSERT INTO sources (name, kind, connection_mode, locator_json, capabilities_json, metadata_json, "
        "map_x, map_y, rotation_deg, fov_deg, calibration_json, calibration_revision, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("Overhead center", "webcam", "agent_local", json.dumps({"device_index": 0}),
         json.dumps(["video"]), json.dumps({"purpose": "central floor coverage"}),
         10.0, 6.2, 90, 110, json.dumps(cal2), 1, NOW))
    # 3 — fridge cam, placed but uncalibrated (shows that state too)
    ids["fridge"] = db.ex(
        "INSERT INTO sources (name, kind, connection_mode, locator_json, capabilities_json, metadata_json, "
        "map_x, map_y, rotation_deg, fov_deg, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("Fridge cam", "file", "agent_local", json.dumps({"local_secret_ref": "fridge_demo_video"}),
         json.dumps(["video"]), json.dumps({"purpose": "fridge state monitoring"}),
         1.2, 4.6, 300, 60, NOW))
    # 4 — a non-visual "sensor" source for the measurement demo (no camera at all)
    ids["hall_sensor"] = db.ex(
        "INSERT INTO sources (name, kind, connection_mode, locator_json, capabilities_json, metadata_json, created_at)"
        " VALUES (?,?,?,?,?,?,?)",
        ("Hall occupancy sensor", "sensor", "agent_local", "{}", "[]",
         json.dumps({"purpose": "synthetic occupancy_estimate measurement"}), NOW))
    print(f"sources: entrance cam (calibrated ±{err:.2f}m), overhead (±{err2:.2f}m), fridge cam, hall sensor")
    return ids


def zone_centroid(z):
    pts = z["polygon"]
    return (sum(p["x"] for p in pts) / len(pts), sum(p["y"] for p in pts) / len(pts))


def _obs_row(job, src, ts, kind, track=None, zone=None, xm=None, ym=None, value=None,
            label=None, name=None, entity_type=None, value_kind=None, attrs=None):
    """Build one `events` row exactly as server/services/enrich.py would have
    produced it for a submitted observation. The seed script writes rows
    directly (bypassing the HTTP round-trip for speed), so it must replicate
    the same zone-assignment enrichment a real POST /observations/batch call
    would perform — not skip it."""
    return (
        job, src, ts, kind, track, zone, None, None, xm, ym, value, label,
        None, None, None, None, None, None,
        "map_point" if zone is not None else None, "worker_point_map" if xm is not None else None,
        None, None, None, None, json.dumps(attrs or {}), NOW,
        2, str(uuid.uuid4()), None, name, entity_type, value_kind, None, None, "worker_run", None,
    )


INSERT_EVENTS_SQL = (
    "INSERT INTO events (job_id,source_id,ts,event_type,track_id,zone_id,x_px,y_px,x_map,y_map,"
    "value,label,bbox_json,keypoints_json,mask_json,point_kind,projection_surface_id,zone_view_id,"
    "zone_assignment_method,projection_method,zone_revision,calibration_revision,surface_revision,"
    "zone_view_revision,attributes,created_at,schema_version,observation_id,worker_id,name,"
    "entity_type,value_kind,unit,confidence,identity_scope,identity_model_version)"
    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
)


def simulate_history(zones: dict, source_ids: dict):
    job_shoppers = db.ex(
        "INSERT INTO jobs (name, description, source_ids, event_types, status, created_at) VALUES (?,?,?,?,?,?)",
        ("Seed: shopper history", "3h of synthetic shopper traffic (tracked detections only)",
         json.dumps([source_ids["entrance"], source_ids["overhead"]]),
         json.dumps(["detection"]), "active", NOW - HISTORY_S))
    job_fridge = db.ex(
        "INSERT INTO jobs (name, description, source_ids, event_types, status, created_at) VALUES (?,?,?,?,?,?)",
        ("Seed: fridge monitor", "repeated door-state samples (synthetic)",
         json.dumps([source_ids["fridge"]]), json.dumps(["state"]), "active", NOW - HISTORY_S))
    job_hall = db.ex(
        "INSERT INTO jobs (name, description, source_ids, event_types, status, created_at) VALUES (?,?,?,?,?,?)",
        ("Seed: hall occupancy", "synthetic occupancy_estimate gauge measurement",
         json.dumps([source_ids["hall_sensor"]]), json.dumps(["measurement"]), "active", NOW - HISTORY_S))

    rows = []

    def add(job, src, ts, kind, **kwargs):
        rows.append(_obs_row(job, src, ts, kind, **kwargs))

    entrance, checkout = zones["Entrance"], zones["Checkout"]
    browse_pool = [zones["Aisle A"], zones["Aisle B"], zones["Fridge"], zones["Promo corner"]]
    all_zones = list(zones.values())

    def zone_at(x, y):
        for z in all_zones:
            if homography.point_in_polygon(x, y, z["polygon"]):
                return z["id"]
        return None

    n_shoppers = 55
    for i in range(n_shoppers):
        tid = f"seed{i + 1}"
        attrs = {"gender": random.choice(["female", "male"]),
                 "basket": random.choice(["yes", "yes", "no"])}
        # A handful of shoppers are still "walking" right up to NOW so the
        # current-detections read model has something to show immediately.
        latest_start = HISTORY_S - 600 if i % 7 else HISTORY_S - 30
        t = NOW - HISTORY_S + random.uniform(0, latest_start)
        src = source_ids["overhead"] if random.random() < 0.7 else source_ids["entrance"]
        stops = random.sample(browse_pool, k=random.randint(1, 3))
        path = [entrance] + stops + ([checkout] if random.random() < 0.85 else []) + [entrance]
        pauses = {"entrance": (2, 6), "checkout": (25, 190), "fridge": (12, 50),
                  "aisle": (15, 90), "area": (10, 60)}
        pos = list(zone_centroid(entrance))

        for wp in path[1:]:
            tx, ty = zone_centroid(wp)
            tx += random.uniform(-0.6, 0.6)
            ty += random.uniform(-0.6, 0.6)
            while True:  # walk toward waypoint
                dx, dy = tx - pos[0], ty - pos[1]
                d = (dx * dx + dy * dy) ** 0.5
                if d < 0.4:
                    break
                step = min(random.uniform(0.9, 1.6), d)
                pos[0] += dx / d * step + random.uniform(-0.1, 0.1)
                pos[1] += dy / d * step + random.uniform(-0.1, 0.1)
                t += 1.5
                if random.random() < 0.5:
                    add(job_shoppers, src, t, "detection", track=tid, zone=zone_at(*pos),
                       xm=pos[0], ym=pos[1], entity_type="person", label="customer", attrs=attrs)
            lo, hi = pauses.get(wp["ztype"], (10, 40))
            pause = random.uniform(lo, hi)
            t_pause_end = t + pause
            while t < t_pause_end:  # browse in place -- repeated same-zone samples,
                t += 5              # exactly what derive_visits_from_detections expects
                jx = pos[0] + random.uniform(-0.3, 0.3)
                jy = pos[1] + random.uniform(-0.3, 0.3)
                add(job_shoppers, src, t, "detection", track=tid, zone=zone_at(jx, jy),
                   xm=jx, ym=jy, entity_type="person", label="customer", attrs=attrs)

    # Fridge door: a `state` sample every ~30s, including long runs of repeated
    # identical labels -- ManySight coalesces these into intervals itself.
    t, state = NOW - HISTORY_S, "closed"
    next_flip = t + random.uniform(300, 900)
    while t < NOW:
        add(job_fridge, source_ids["fridge"], t, "state", name="door_state", label=state)
        t += 30
        if t >= next_flip:
            state = "open" if state == "closed" else "closed"
            next_flip = t + (random.uniform(20, 200) if state == "open" else random.uniform(300, 900))

    # Hall occupancy: a gauge measurement every 5 minutes with a plausible
    # morning/lunch/afternoon curve -- a measurement-subject analytics demo
    # that has no natural single entity, so it's zone-assigned via geometry.
    hall_point = zone_centroid(entrance)
    samples = int(HISTORY_S / 300)
    for i in range(samples + 1):
        ts = NOW - HISTORY_S + i * 300
        progress = i / samples
        value = max(0, round(
            4 + 30 * pow(2.71828, -((progress - 0.35) / 0.12) ** 2)
              + 22 * pow(2.71828, -((progress - 0.75) / 0.15) ** 2)
              + random.uniform(-3, 3)))
        add(job_hall, source_ids["hall_sensor"], ts, "measurement", name="occupancy_estimate",
           value=value, value_kind="gauge", xm=hall_point[0], ym=hall_point[1],
           zone=zone_at(*hall_point), attrs={"model": "demo-simulator"})

    db.exmany(INSERT_EVENTS_SQL, rows)
    for jid in (job_shoppers, job_fridge, job_hall):
        stats = db.q1("SELECT COUNT(*) n, MAX(ts) m FROM events WHERE job_id=?", (jid,))
        db.ex("UPDATE jobs SET event_count=?, last_event_at=? WHERE id=?", (stats["n"], stats["m"], jid))
    print(f"observations: {len(rows)} over the last 3h ({n_shoppers} shoppers, fridge cycle, hall occupancy)")


def seed_alerts(zones: dict, source_ids: dict):
    db.ex("DELETE FROM alert_rules")
    db.ex("DELETE FROM alerts")
    r1 = db.ex("INSERT INTO alert_rules (name, kind, params_json, cooldown_s, enabled, created_at) VALUES (?,?,?,?,1,?)",
               ("Loitering at checkout", "dwell_exceeds",
                json.dumps({"zone_id": zones["Checkout"]["id"], "seconds": 120}), 300, NOW))
    r2 = db.ex("INSERT INTO alert_rules (name, kind, params_json, cooldown_s, enabled, created_at) VALUES (?,?,?,?,1,?)",
               ("Fridge left open ≥2min", "state_alert",
                json.dumps({"label": "open", "name": "door_state", "min_seconds": 120,
                           "source_id": source_ids["fridge"]}), 300, NOW))
    db.ex("INSERT INTO alert_rules (name, kind, params_json, cooldown_s, enabled, created_at) VALUES (?,?,?,?,1,?)",
          ("Crowd at entrance", "occupancy_exceeds",
           json.dumps({"zone_id": zones["Entrance"]["id"], "count": 6, "window_s": 60}), 300, NOW))
    # Unified analysis_condition rule -- the general case, evaluated periodically
    # (services/alert_engine.py:evaluate_ongoing), not just on ingestion.
    db.ex(
        "INSERT INTO alert_rules (name, kind, analysis_json, condition_json, cooldown_s, enabled, created_at)"
        " VALUES (?,?,?,?,?,1,?)",
        ("Hall occupancy spike", "analysis_condition",
         json.dumps({"subject": "measurement", "measures": ["latest"],
                    "filters": {"measurement_names": ["occupancy_estimate"]}}),
         json.dumps({"operator": ">", "value": 40, "for_seconds": 300, "window_s": 900}),
         300, NOW))
    # a few historical alerts so the log isn't empty
    for ts, rule, title, msg in (
        (NOW - 5400, r1, "Loitering at checkout", "Track seed12 dwelled 168s in Checkout (limit 120s)"),
        (NOW - 3200, r2, "Fridge left open ≥2min", "State 'open' lasted 187s (limit 120s)"),
        (NOW - 900, r1, "Loitering at checkout", "Track seed41 dwelled 141s in Checkout (limit 120s)"),
    ):
        db.ex("INSERT INTO alerts (rule_id, ts, title, message, payload_json, acknowledged, created_at) VALUES (?,?,?,?,?,0,?)",
              (rule, ts, title, msg, "{}", NOW))
    print("alerts: 4 rules (incl. 1 unified analysis_condition), 3 historical alerts")


def seed_analyses(zones: dict, source_ids: dict):
    """Saved analyses in the unified model — a question (subject/measures/
    filters/grouping), never a chart. Legacy insight_definitions rows (if any
    survive from an older DB) are migrated separately, once, in db.init_db()."""
    db.ex("DELETE FROM analyses WHERE migrated_from_insight_id IS NULL")
    definitions = [
        ("Visitor presence over time", "How many people are present over time?",
         "detection", ["active_entities"], {}, {"primary": "time", "bucket": "10m"}, "", 1, 0),
        ("Activity heatmap", "Where does activity concentrate on the floor?",
         "detection", ["density"], {}, {}, "heatmap_map", 1, 1),
        ("Dwell by zone", "How long do visitors stay in each zone, and how many visits?",
         "detection", ["visits", "average_dwell", "total_dwell"], {}, {"primary": "zone"}, "table", 0, 2),
        ("Zone-to-zone flow", "How do people move between zones?",
         "detection", ["transition_count"], {}, {}, "flow_matrix", 0, 3),
        ("Fridge door states", "How long does the fridge stay open?",
         "state", ["duration", "time_percentage"], {"source_ids": [source_ids["fridge"]]}, {}, "state_timeline", 0, 4),
        ("Hall occupancy trend", "How does main-hall occupancy change over the day?",
         "measurement", ["latest", "average"], {"measurement_names": ["occupancy_estimate"]},
         {"primary": "time", "bucket": "15m"}, "line", 1, 5),
        ("Dwell by gender", "Does dwell time differ by the worker-reported gender attribute?",
         "detection", ["average_dwell"], {}, {"primary": "zone", "split_by": ["attribute:gender"]}, "bar", 0, 6),
    ]
    now = time.time()
    for name, question, subject, measures, filters, grouping, presentation, pinned, order in definitions:
        query_hash = db.analysis_hash(subject, measures, filters, grouping)
        db.ex(
            "INSERT INTO analyses (name, question, subject, measures_json, filters_json, grouping_json,"
            " default_range_json, comparison_json, presentation, pinned, sort_order, visibility,"
            " created_by, status, query_hash, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,'{}','{}',?,?,?,'visible','user','ready',?,?,?)",
            (name, question, subject, json.dumps(measures), json.dumps(filters), json.dumps(grouping),
             presentation, pinned, order, query_hash, now, now))
    print(f"analyses: {len(definitions)} registered (2 pinned to Dashboard)")


if __name__ == "__main__":
    db.init_db()
    clear_previous_seed()
    seed_store()
    zones = seed_zones()
    source_ids = seed_sources()
    simulate_history(zones, source_ids)
    seed_alerts(zones, source_ids)
    seed_analyses(zones, source_ids)
    print("\nSeed complete. Start the server and open http://localhost:8000")
    print("  uvicorn server.app:app --port 8000")
    print("For live streaming observations on top: python examples/simulate_shoppers.py")
