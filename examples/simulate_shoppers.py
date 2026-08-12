"""Live shopper simulator — demo the whole platform with zero cameras.

Registers a job and submits realistic raw `detection` and `state` observations
in real time, so Live, generated queries, and alerts can be exercised. This
worker never resolves a zone, pairs an enter/exit, or computes
a state change — it only reports what it "observed" (a simulated position, or
a simulated fridge-door reading) every tick; StoreLens derives zones, visits,
dwell, transitions, and state durations from those raw rows.

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

    def __init__(self, zones):
        Shopper._n += 1
        self.id = f"sim{Shopper._n}"
        self.attrs = {"gender": random.choice(["female", "male"])}
        entrance = next((z for z in zones if z["ztype"] == "entrance"), zones[0])
        checkout = [z for z in zones if z["ztype"] == "checkout"]
        browse = [z for z in zones if z["ztype"] in ("aisle", "fridge", "area")] or zones
        stops = random.sample(browse, k=min(len(browse), random.randint(1, 3)))
        path = [entrance] + stops + (checkout and [random.choice(checkout)] or []) + [entrance]
        self.waypoints = [centroid(z) for z in path]
        self.pos = list(self.waypoints[0])
        self.wp = 1
        self.pause_until = 0
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


def simulated_source(sl: StoreLens) -> dict:
    """This simulator has no camera — every observation still needs a
    source_id, so reuse or create one logical, credential-free 'sensor' source
    to attribute the synthetic observations to."""
    existing = next((s for s in sl.sources() if s["name"] == "Shopper simulator"), None)
    if existing:
        return existing
    return sl.create_source(name="Shopper simulator", kind="sensor", connection_mode="agent_local",
                            metadata={"purpose": "synthetic demo traffic, no real camera"})


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
    source = simulated_source(sl)
    sl.register_job("Live shopper simulation", "synthetic shoppers walking waypoint paths",
                    source_ids=[source["id"]], event_types=["detection", "state"])
    sl.register_worker("shopper-simulator", version="1")
    print("Contract: this worker sends only 'detection' and 'state' observations — "
          "StoreLens derives zone visits, dwell, transitions, and door-state durations.")
    fridge_state, fridge_next = "closed", time.time() + random.uniform(20, 60)
    shoppers = [Shopper(zones) for _ in range(args.shoppers)]
    t_end = time.time() + args.minutes * 60
    last_heartbeat = 0.0
    print(f"Simulating {args.shoppers} shoppers for {args.minutes} min → {args.url}")

    while time.time() < t_end:
        now = time.time()
        if now - last_heartbeat >= 10:
            command = sl.heartbeat(metrics={"active_shoppers": len(shoppers)})
            last_heartbeat = now
            if command["should_stop"]:
                break
        for s in list(shoppers):
            s.step(1.0, now)
            if s.done:
                shoppers.remove(s)
                shoppers.append(Shopper(zones))
                continue
            # This simulator has no camera frame, so it reports the map position
            # directly (point_map) instead of pixel evidence — see submit_detection's
            # docstring on when that's appropriate.
            sl.submit_detection(source_id=source["id"], entity_id=s.id, point_map=s.pos,
                               entity_type="person", label="customer", attributes=s.attrs)
        if now >= fridge_next:
            new = "open" if fridge_state == "closed" else "closed"
            sl.submit_state(source_id=source["id"], name="fridge_door", label=new)
            fridge_state = new
            fridge_next = now + (random.uniform(15, 90) if new == "open" else random.uniform(60, 240))
        else:
            # Repeated identical samples are expected and required — StoreLens
            # coalesces them; a worker must not try to detect the change itself.
            sl.submit_state(source_id=source["id"], name="fridge_door", label=fridge_state)
        sl.flush()
        time.sleep(1.0)
    sl.flush()
    sl.stop_worker()
    print("done.")


if __name__ == "__main__":
    main()
