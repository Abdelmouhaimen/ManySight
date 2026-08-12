"""Dwell worker — tracked detections from a real video source.

Tracks people and submits `detection` observations with feet pixel points and a
stable per-track `entity_id`, plus one processed-frame count (including zero).
This worker never
resolves a zone, debounces a boundary crossing, or pairs an enter/exit — the
platform projects each point through the source calibration, matches it against
zone geometry with its own hysteresis rules, and derives visits and dwell
duration from the resulting stream of zoned detections (services/derive.py).
Requires a calibrated source so points can be projected onto zone polygons.

Usage:
    python examples/dwell_zones.py --source 1 [--zones "Checkout,Fridge"]
"""
import sys
import time

sys.path.insert(0, "sdk/python")
from storelens import StoreLens, CentroidTracker, parse_args_base  # noqa: E402
from heatmap_tracker import motion_detector, submit_tracked_frame  # noqa: E402


def main():
    ap = parse_args_base(__doc__)
    ap.add_argument("--zones", default="", help="comma-separated zone names, informational only")
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
        print("Note: no matching zones on the map yet — detections still post; dwell/visits "
              "appear once zones are drawn in the Store Map tab.")
    else:
        print(f"zones on this floor plan: {[z['name'] for z in zones]} "
              "(StoreLens assigns them from geometry — this worker never resolves one itself)")

    sl.register_job(f"Dwell – {src['name']}", "tracked detections for dwell/visit derivation",
                    source_ids=[src["id"]], event_types=["detection", "measurement"])
    sl.register_worker("dwell-zones", version="1")
    print("Contract: every processed frame sends detections followed by one exact-timestamp "
          "frame count, including zero; StoreLens derives zone visits and dwell duration.")
    detect = motion_detector()
    cap = sl.open_capture(src, args.connection)
    tracker = CentroidTracker(max_distance=90)
    last_heartbeat = 0.0

    while True:
        ok, frame = cap.read()
        if not ok:
            break
        now = time.time()
        if now - last_heartbeat >= 10:
            command = sl.heartbeat(metrics={"tracked": len(tracker.tracks)})
            last_heartbeat = now
            if command["should_stop"]:
                break
        sample_ts = time.time()
        tracks = tracker.update(detect(frame))
        submit_tracked_frame(sl, src["id"], tracks, sample_ts, "dwell_zones")
        sl.flush_observations()
        time.sleep(0.05)
    sl.flush()
    sl.stop_worker()


if __name__ == "__main__":
    main()
