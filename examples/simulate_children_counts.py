"""Post a realistic children-in-main-hall count curve for UI testing.

This is a dashboard/observation-contract simulator, not a vision model. It lets
you verify the complete StoreLens measurement workflow before connecting a real
camera. Each row is a `measurement` observation (name="children_present",
value_kind="gauge") — an instantaneous population sample, never a precomputed
average or a time-aggregated total.

Examples:
    python examples/simulate_children_counts.py
    python examples/simulate_children_counts.py --zone "Main hall" --hours 6
"""
import argparse
import math
import os
import random
import sys
import time
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "sdk", "python"))
from storelens import StoreLens  # noqa: E402


def _zone_centroid(zone):
    pts = zone["polygon"]
    return (sum(p["x"] for p in pts) / len(pts), sum(p["y"] for p in pts) / len(pts))


def main():
    ap = argparse.ArgumentParser(description="Simulate a school hall population curve")
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--api-key", default="")
    ap.add_argument("--zone", default="Main hall")
    ap.add_argument("--hours", type=float, default=3)
    ap.add_argument("--interval", type=int, default=60, help="seconds between samples")
    ap.add_argument("--source", type=int, default=None, help="optional camera source id")
    args = ap.parse_args()

    random.seed(7)
    sl = StoreLens(args.url, args.api_key)
    zone = sl.zone_by_name(args.zone)
    point_map = None
    if zone is None:
        print(f"Note: zone '{args.zone}' does not exist; counts will still render without a zone.")
        print("Create it in the Space tab if you want zone-filtered analytics.")
    else:
        # A population count has no single associated entity, so it can only be
        # zone-assigned if it carries geometry — the zone's centroid is a
        # reasonable stand-in for "measured somewhere inside this zone".
        point_map = _zone_centroid(zone)

    source_id = args.source
    if source_id is None:
        existing = next((s for s in sl.sources() if s["name"] == "Children-count simulator"), None)
        source_id = (existing or sl.create_source(
            name="Children-count simulator", kind="sensor", connection_mode="agent_local",
            metadata={"purpose": "synthetic demo measurement, no real camera"}))["id"]

    job = sl.register_job(
        "Demo: children in main hall",
        "Synthetic measurement samples for testing the classifier-count time series",
        source_ids=[source_id], event_types=["measurement"],
    )
    sl.register_worker("children-count-simulator", version="1")
    sl.heartbeat(metrics={"mode": "finite_backfill"})
    now = time.time()
    start = now - args.hours * 3600
    samples = max(2, int(args.hours * 3600 / args.interval))
    observations = []
    for i in range(samples + 1):
        ts = start + i * args.interval
        progress = i / samples
        # Quiet baseline with two bell/changeover peaks and a smaller late peak.
        count = 3
        count += 50 * math.exp(-((progress - 0.28) / 0.075) ** 2)
        count += 72 * math.exp(-((progress - 0.58) / 0.09) ** 2)
        count += 30 * math.exp(-((progress - 0.82) / 0.065) ** 2)
        count = max(0, round(count + random.uniform(-3, 3)))
        observation = {
            "schema_version": 2, "observation_id": str(uuid.uuid4()), "kind": "measurement",
            "timestamp": ts, "source_id": source_id, "name": "children_present",
            "value": count, "value_kind": "gauge",
            "attributes": {"population": "children", "model": "demo-simulator"},
        }
        if point_map:
            observation["geometry"] = {"point_map": {"x": point_map[0], "y": point_map[1]}}
        observations.append(observation)

    result = sl.submit_observations(observations)
    sl.stop_worker()
    print(f"Posted {result['accepted']} child-count samples to job {job['id']} "
         f"({result['duplicates']} duplicates, {len(result['rejected'])} rejected).")
    print("Create a saved query and dashboard widget covering the last few hours.")


if __name__ == "__main__":
    main()
