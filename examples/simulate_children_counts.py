"""Post a realistic children-in-main-hall count curve for UI testing.

This is a dashboard/event-contract simulator, not a vision model. It lets you verify
the complete StoreLens count workflow before connecting a real camera.

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

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "sdk", "python"))
from storelens import StoreLens  # noqa: E402


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
    if zone is None:
        print(f"Note: zone '{args.zone}' does not exist; counts will still render without a zone.")
        print("Create it in the Space tab if you want zone-filtered analytics.")

    job = sl.register_job(
        "Demo: children in main hall",
        "Synthetic count samples for testing the classifier-count time series",
        source_ids=[args.source] if args.source else [],
        event_types=["count"],
    )
    now = time.time()
    start = now - args.hours * 3600
    samples = max(2, int(args.hours * 3600 / args.interval))
    events = []
    for i in range(samples + 1):
        ts = start + i * args.interval
        progress = i / samples
        # Quiet baseline with two bell/changeover peaks and a smaller late peak.
        count = 3
        count += 50 * math.exp(-((progress - 0.28) / 0.075) ** 2)
        count += 72 * math.exp(-((progress - 0.58) / 0.09) ** 2)
        count += 30 * math.exp(-((progress - 0.82) / 0.065) ** 2)
        count = max(0, round(count + random.uniform(-3, 3)))
        event = {
            "ts": ts,
            "source_id": args.source,
            "event_type": "count",
            "label": "children",
            "value": count,
            "attributes": {"population": "children", "model": "demo-simulator"},
        }
        if zone:
            event["zone_id"] = zone["id"]
        events.append(event)

    result = sl.post_events(events, job_id=job["id"])
    print(f"Posted {result['inserted']} child-count samples to job {job['id']}.")
    print("Open Insights and choose a range covering the last 3 hours.")


if __name__ == "__main__":
    main()
