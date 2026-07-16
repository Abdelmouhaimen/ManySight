"""Live shopper simulator — demo the whole platform with zero cameras.

Registers a job and streams realistic synthetic events (detections, zone enter/exit/
dwell, fridge state changes) in real time, so the Insights tab, live feed and alerts
all light up.

Usage:
    python examples/simulate_shoppers.py --url http://localhost:8000 --shoppers 6 --minutes 10
Requires zones on the map (run scripts/seed_demo.py first, or draw your own).
"""
import argparse
import random
import sys
import time

sys.path.insert(0, "sdk/python")
from storelens import StoreLens  # noqa: E402


def centroid(zone):
    pts = zone["polygon"]
    return (sum(p["x"] for p in pts) / len(pts), sum(p["y"] for p in pts) / len(pts))


class Shopper:
    _n = 0

    def __init__(self, zones, store):
        Shopper._n += 1
        self.id = f"sim{Shopper._n}"
        self.attrs = {"gender": random.choice(["female", "male"])}
        self.store = store
        entrance = next((z for z in zones if z["ztype"] == "entrance"), zones[0])
        checkout = [z for z in zones if z["ztype"] == "checkout"]
        browse = [z for z in zones if z["ztype"] in ("aisle", "fridge", "area")] or zones
        stops = random.sample(browse, k=min(len(browse), random.randint(1, 3)))
        path = [entrance] + stops + (checkout and [random.choice(checkout)] or []) + [entrance]
        self.waypoints = [centroid(z) for z in path]
        self.pos = list(self.waypoints[0])
        self.wp = 1
        self.pause_until = 0
        self.zone_state = {}       # zone_id -> enter_ts
        self.done = False

    def step(self, dt, now):
        if self.done or now < self.pause_until:
            return
        tx, ty = self.waypoints[self.wp]
        dx, dy = tx - self.pos[0], ty - self.pos[1]
        d = (dx * dx + dy * dy) ** 0.5
        speed = random.uniform(0.6, 1.2)
        if d < 0.3:
            self.pause_until = now + random.uniform(3, 25)   # browse / queue
            self.wp += 1
            if self.wp >= len(self.waypoints):
                self.done = True
            return
        self.pos[0] += dx / d * speed * dt + random.uniform(-0.08, 0.08)
        self.pos[1] += dy / d * speed * dt + random.uniform(-0.08, 0.08)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--api-key", default="")
    ap.add_argument("--shoppers", type=int, default=6)
    ap.add_argument("--minutes", type=float, default=10)
    args = ap.parse_args()

    sl = StoreLens(args.url, args.api_key, batch_size=50)
    store = sl.store_map()
    zones = store["zones"]
    if not zones:
        raise SystemExit("No zones defined — run scripts/seed_demo.py or draw zones in the Store Map tab.")
    sl.register_job("Live shopper simulation", "synthetic shoppers walking waypoint paths",
                    event_types=["detection", "zone_enter", "zone_exit", "zone_dwell", "state_change"])
    fridge_state, fridge_since, fridge_next = "closed", time.time(), time.time() + random.uniform(20, 60)
    shoppers = [Shopper(zones, store) for _ in range(args.shoppers)]
    t_end = time.time() + args.minutes * 60
    print(f"Simulating {args.shoppers} shoppers for {args.minutes} min → {args.url}")

    while time.time() < t_end:
        now = time.time()
        for s in list(shoppers):
            s.step(1.0, now)
            if s.done:
                for zid, t0 in s.zone_state.items():
                    sl.add_event(event_type="zone_exit", track_id=s.id, zone_id=zid, attributes=s.attrs)
                    sl.add_event(event_type="zone_dwell", track_id=s.id, zone_id=zid, value=now - t0, attributes=s.attrs)
                shoppers.remove(s)
                shoppers.append(Shopper(zones, store))
                continue
            sl.add_event(event_type="detection", track_id=s.id,
                         point_map={"x": s.pos[0], "y": s.pos[1]}, attributes=s.attrs)
            for z in zones:
                member = sl.point_in_zone(z, s.pos[0], s.pos[1])
                if member and z["id"] not in s.zone_state:
                    s.zone_state[z["id"]] = now
                    sl.add_event(event_type="zone_enter", track_id=s.id, zone_id=z["id"], attributes=s.attrs)
                elif not member and z["id"] in s.zone_state:
                    t0 = s.zone_state.pop(z["id"])
                    sl.add_event(event_type="zone_exit", track_id=s.id, zone_id=z["id"], attributes=s.attrs)
                    sl.add_event(event_type="zone_dwell", track_id=s.id, zone_id=z["id"], value=now - t0, attributes=s.attrs)
        if now >= fridge_next:
            new = "open" if fridge_state == "closed" else "closed"
            sl.add_event(event_type="state_change", label=new, value=now - fridge_since,
                         attributes={"prev_label": fridge_state},
                         zone=next((z["name"] for z in zones if z["ztype"] == "fridge"), None))
            fridge_state, fridge_since = new, now
            fridge_next = now + (random.uniform(15, 90) if new == "open" else random.uniform(60, 240))
        sl.flush()
        time.sleep(1.0)
    sl.flush()
    print("done.")


if __name__ == "__main__":
    main()
