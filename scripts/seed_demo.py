"""Seed StoreLens with a complete, realistic demo:
store plan, zones, cameras (with a real computed homography), jobs, ~3 hours of
synthetic shopper history (raw observations only — the platform derives dwell),
fridge open/close states, alert rules, fired alerts, and an insight catalogue.

Run once (server may be running or not — writes the same SQLite DB):
    python scripts/seed_demo.py
Then open http://localhost:8000 — every tab is populated.
"""
import json
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import db  # noqa: E402
from server.services import homography, snapshots  # noqa: E402

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
        "INSERT INTO sources (name, kind, url, username, password, status, map_x, map_y, rotation_deg, fov_deg, calibration_json, calibration_revision, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("Entrance cam", "rtsp", "rtsp://192.168.1.10:554/stream1", "demo", "demo123",
         "unknown", 18.6, 11.2, 211, 85, json.dumps(cal), 1, NOW))
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
        "INSERT INTO sources (name, kind, url, status, map_x, map_y, rotation_deg, fov_deg, calibration_json, calibration_revision, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("Overhead center", "webcam", "0", "unknown", 10.0, 6.2, 90, 110, json.dumps(cal2), 1, NOW))
    # 3 — fridge cam, placed but uncalibrated (shows that state too)
    ids["fridge"] = db.ex(
        "INSERT INTO sources (name, kind, url, status, map_x, map_y, rotation_deg, fov_deg, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        ("Fridge cam", "file", "./data/videos/fridge_demo.mp4", "unknown", 1.2, 4.6, 300, 60, NOW))
    # placeholder snapshots so every card shows an image
    for key, sid in ids.items():
        row = db.q1("SELECT * FROM sources WHERE id=?", (sid,))
        png = snapshots.placeholder_png(row, "seeded demo — click ⟳ test for a real frame")
        with open(snapshots.snapshot_path(sid), "wb") as f:
            f.write(png)
    print(f"sources: entrance cam (calibrated ±{err:.2f}m), overhead (±{err2:.2f}m), fridge cam")
    return ids


def zone_centroid(z):
    pts = z["polygon"]
    return (sum(p["x"] for p in pts) / len(pts), sum(p["y"] for p in pts) / len(pts))


def simulate_history(zones: dict, source_ids: dict):
    job_shoppers = db.ex(
        "INSERT INTO jobs (name, description, source_ids, event_types, status, created_at) VALUES (?,?,?,?,?,?)",
        ("Seed: shopper history", "3h of synthetic shopper traffic (waypoint walks with zone bookkeeping)",
         json.dumps([source_ids["entrance"], source_ids["overhead"]]),
         json.dumps(["detection", "zone_enter", "zone_exit"]), "active", NOW - HISTORY_S))
    job_fridge = db.ex(
        "INSERT INTO jobs (name, description, source_ids, event_types, status, created_at) VALUES (?,?,?,?,?,?)",
        ("Seed: fridge monitor", "door open/closed states via ROI diff (synthetic)",
         json.dumps([source_ids["fridge"]]), json.dumps(["state_change"]), "active", NOW - HISTORY_S))

    rows = []  # (job, source, ts, etype, track, zone, x_px, y_px, x_map, y_map, value, label, attrs)

    def add(job, src, ts, etype, track=None, zone=None, xm=None, ym=None, value=None, label=None, attrs=None):
        rows.append((job, src, ts, etype, track, zone, None, None, xm, ym, value, label,
                     json.dumps(attrs or {}), NOW))

    entrance, checkout = zones["Entrance"], zones["Checkout"]
    browse_pool = [zones["Aisle A"], zones["Aisle B"], zones["Fridge"], zones["Promo corner"]]
    all_zones = list(zones.values())

    n_shoppers = 55
    for i in range(n_shoppers):
        tid = f"seed{i + 1}"
        attrs = {"gender": random.choice(["female", "male"]),
                 "basket": random.choice(["yes", "yes", "no"])}
        t = NOW - HISTORY_S + random.uniform(0, HISTORY_S - 600)
        src = source_ids["overhead"] if random.random() < 0.7 else source_ids["entrance"]
        stops = random.sample(browse_pool, k=random.randint(1, 3))
        path = [entrance] + stops + ([checkout] if random.random() < 0.85 else []) + [entrance]
        pauses = {"entrance": (2, 6), "checkout": (25, 190), "fridge": (12, 50),
                  "aisle": (15, 90), "area": (10, 60)}
        pos = list(zone_centroid(entrance))
        in_zone = {}

        def update_zones(ts, x, y):
            # raw observations only: enter/exit pairs — the platform derives dwell
            for z in all_zones:
                member = homography.point_in_polygon(x, y, z["polygon"])
                if member and z["id"] not in in_zone:
                    in_zone[z["id"]] = ts
                    add(job_shoppers, src, ts, "zone_enter", tid, z["id"], x, y, attrs=attrs)
                elif not member and z["id"] in in_zone:
                    in_zone.pop(z["id"])
                    add(job_shoppers, src, ts, "zone_exit", tid, z["id"], x, y, attrs=attrs)

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
                    add(job_shoppers, src, t, "detection", tid, None, pos[0], pos[1], attrs=attrs)
                update_zones(t, pos[0], pos[1])
            lo, hi = pauses.get(wp["ztype"], (10, 40))
            pause = random.uniform(lo, hi)
            t_pause_end = t + pause
            while t < t_pause_end:  # browse in place
                t += 5
                jx = pos[0] + random.uniform(-0.3, 0.3)
                jy = pos[1] + random.uniform(-0.3, 0.3)
                add(job_shoppers, src, t, "detection", tid, None, jx, jy, attrs=attrs)
                update_zones(t, jx, jy)
        for zid in list(in_zone):  # close out open visits with exits
            add(job_shoppers, src, t, "zone_exit", tid, zid, pos[0], pos[1], attrs=attrs)

    # fridge open/close cycle: label-only flips, durations derived by the platform
    t = NOW - HISTORY_S
    state = "closed"
    fridge_zone = zones["Fridge"]["id"]
    add(job_fridge, source_ids["fridge"], t, "state_change", zone=fridge_zone, label=state)
    while t < NOW - 60:
        dur = random.uniform(300, 900) if state == "closed" else random.uniform(20, 200)
        t += dur
        if t >= NOW - 60:
            break
        state = "open" if state == "closed" else "closed"
        add(job_fridge, source_ids["fridge"], t, "state_change", zone=fridge_zone, label=state)

    db.exmany(
        "INSERT INTO events (job_id, source_id, ts, event_type, track_id, zone_id, x_px, y_px, x_map, y_map, value, label, attributes, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    for jid in (job_shoppers, job_fridge):
        stats = db.q1("SELECT COUNT(*) n, MAX(ts) m FROM events WHERE job_id=?", (jid,))
        db.ex("UPDATE jobs SET event_count=?, last_event_at=? WHERE id=?", (stats["n"], stats["m"], jid))
    print(f"events: {len(rows)} over the last 3h ({n_shoppers} shoppers + fridge cycle)")


def seed_alerts(zones: dict, source_ids: dict):
    db.ex("DELETE FROM alert_rules")
    db.ex("DELETE FROM alerts")
    r1 = db.ex("INSERT INTO alert_rules (name, kind, params_json, cooldown_s, enabled, created_at) VALUES (?,?,?,?,1,?)",
               ("Loitering at checkout", "dwell_exceeds",
                json.dumps({"zone_id": zones["Checkout"]["id"], "seconds": 120}), 300, NOW))
    r2 = db.ex("INSERT INTO alert_rules (name, kind, params_json, cooldown_s, enabled, created_at) VALUES (?,?,?,?,1,?)",
               ("Fridge left open ≥2min", "state_alert",
                json.dumps({"label": "open", "min_seconds": 120, "source_id": source_ids["fridge"]}), 300, NOW))
    db.ex("INSERT INTO alert_rules (name, kind, params_json, cooldown_s, enabled, created_at) VALUES (?,?,?,?,1,?)",
          ("Crowd at entrance", "occupancy_exceeds",
           json.dumps({"zone_id": zones["Entrance"]["id"], "count": 6, "window_s": 60}), 300, NOW))
    # a few historical alerts so the log isn't empty
    for ts, rule, title, msg in (
        (NOW - 5400, r1, "Loitering at checkout", "Track seed12 dwelled 168s in Checkout (limit 120s)"),
        (NOW - 3200, r2, "Fridge left open ≥2min", "State 'open' lasted 187s (limit 120s)"),
        (NOW - 900, r1, "Loitering at checkout", "Track seed41 dwelled 141s in Checkout (limit 120s)"),
    ):
        db.ex("INSERT INTO alerts (rule_id, ts, title, message, payload_json, acknowledged, created_at) VALUES (?,?,?,?,?,0,?)",
              (rule, ts, title, msg, "{}", NOW))
    print("alerts: 3 rules, 3 historical alerts")


def seed_insights(zones: dict, source_ids: dict):
    db.ex("DELETE FROM insight_definitions")
    definitions = [
        ("Visitor presence over time", "How many people are present over time?",
         "line", "occupancy", {}, "people",
         "Distinct track IDs per interval — re-identified people count twice.", 1, 0),
        ("Activity heatmap", "Where does activity concentrate on the floor?",
         "heatmap_map", "heatmap", {}, "",
         "Only calibrated detections appear; uncalibrated cameras contribute nothing.", 1, 1),
        ("Dwell by zone", "How long do visitors stay in each zone?",
         "bar", "dwell", {}, "seconds",
         "Derived from enter/exit pairs; in-progress visits are clipped to the window.", 0, 2),
        ("Zone-to-zone flow", "How do people move between zones?",
         "flow_matrix", "transitions", {}, "moves",
         "Counts consecutive zone entries per track; gaps over 30 minutes break a path.", 0, 3),
        ("Fridge door states", "How long does the fridge stay open?",
         "state_timeline", "states", {"source_id": source_ids["fridge"]}, "",
         "Durations derived from state_change timestamps; gaps read as the last known state.", 0, 4),
    ]
    for title, question, block, dataset, params, unit, limitations, pinned, order in definitions:
        db.ex(
            "INSERT INTO insight_definitions (title, question, block, dataset, params_json, unit,"
            " limitations, pinned, sort_order, visibility, created_by, status, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,'visible','user','ready',?,?)",
            (title, question, block, dataset, json.dumps(params), unit, limitations,
             pinned, order, NOW, NOW))
    print(f"insights: {len(definitions)} registered (2 pinned to Overview)")


if __name__ == "__main__":
    db.init_db()
    clear_previous_seed()
    seed_store()
    zones = seed_zones()
    source_ids = seed_sources()
    simulate_history(zones, source_ids)
    seed_alerts(zones, source_ids)
    seed_insights(zones, source_ids)
    print("\nSeed complete. Start the server and open http://localhost:8000")
    print("  uvicorn server.app:app --port 8000")
    print("For live streaming events on top: python examples/simulate_shoppers.py")
