"""Dwell worker — zone enter/exit/dwell events from a real video source.

Tracks people, projects feet to floor meters locally, and emits zone bookkeeping
events (enter/exit + dwell-with-duration on exit). Requires a calibrated source.

Usage:
    python examples/dwell_zones.py --source 1 [--zones "Checkout,Fridge"]
"""
import sys
import time

sys.path.insert(0, "sdk/python")
from storelens import StoreLens, CentroidTracker, parse_args_base  # noqa: E402
from heatmap_tracker import motion_detector  # reuse the fallback detector  # noqa: E402


def main():
    ap = parse_args_base(__doc__)
    ap.add_argument("--zones", default="", help="comma-separated zone names (default: all)")
    args = ap.parse_args()
    sl = StoreLens(args.url, args.api_key)
    src = sl.source(args.source)
    if not src["calibrated"]:
        raise SystemExit("Source must be calibrated (Store Map tab → ⌗) for dwell analysis.")
    zones = sl.zones()
    if args.zones:
        wanted = {z.strip().lower() for z in args.zones.split(",")}
        zones = [z for z in zones if z["name"].lower() in wanted]
    if not zones:
        raise SystemExit("No matching zones — draw them in the Store Map tab first.")
    print(f"watching zones: {[z['name'] for z in zones]}")

    sl.register_job(f"Dwell – {src['name']}", f"dwell in {[z['name'] for z in zones]}",
                    source_ids=[src["id"]], event_types=["zone_enter", "zone_exit", "zone_dwell"])
    detect = motion_detector()
    cap = sl.open_capture(src)
    tracker = CentroidTracker(max_distance=90)
    inside: dict[tuple, float] = {}
    membership_hits: dict[tuple, int] = {}
    DEBOUNCE = 3

    while True:
        ok, frame = cap.read()
        if not ok:
            break
        now = time.time()
        tracked = tracker.update(detect(frame))
        if not tracked:
            continue
        pts = sl.project(src, [(cx, cy) for _, cx, cy in tracked])
        for (tid, _, _), (xm, ym) in zip(tracked, pts):
            for z in zones:
                key = (tid, z["id"])
                member = sl.point_in_zone(z, xm, ym)
                hits = membership_hits.get(key, 0)
                membership_hits[key] = min(hits + 1, DEBOUNCE) if member else max(hits - 1, 0)
                if membership_hits[key] >= DEBOUNCE and key not in inside:
                    inside[key] = now
                    sl.add_event(source_id=src["id"], event_type="zone_enter", track_id=tid, zone_id=z["id"])
                elif membership_hits[key] == 0 and key in inside:
                    t0 = inside.pop(key)
                    sl.add_event(source_id=src["id"], event_type="zone_exit", track_id=tid, zone_id=z["id"])
                    sl.add_event(source_id=src["id"], event_type="zone_dwell", track_id=tid,
                                 zone_id=z["id"], value=now - t0)
        time.sleep(0.05)
    # close out open visits
    now = time.time()
    for (tid, zid), t0 in inside.items():
        sl.add_event(source_id=src["id"], event_type="zone_dwell", track_id=tid, zone_id=zid, value=now - t0)
    sl.flush()


if __name__ == "__main__":
    main()
