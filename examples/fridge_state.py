"""Fridge/door state worker — repeated open/closed samples from a real video source.

Compares a region of interest against a reference frame captured at startup
(door must be CLOSED when the script starts) and submits a `state` observation
on every sample period — including runs of identical samples. This worker never
decides "did it change": ManySight coalesces consecutive identical samples into
intervals and derives transitions, durations, and duration alerts itself
(services/derive.py:coalesce_state_intervals). A worker submitting a `state`
sample every 2s must not try to emit only-on-flip state_change events.

Usage:
    python examples/fridge_state.py --source 2 --roi 100,80,220,300 [--thresh 18]
"""
import sys
import time

sys.path.insert(0, "sdk/python")
from manysight import ManySight, parse_args_base  # noqa: E402


def main():
    import cv2
    import numpy as np

    ap = parse_args_base(__doc__)
    ap.add_argument("--roi", required=True, help="x,y,w,h in pixels around the door")
    ap.add_argument("--thresh", type=float, default=18.0, help="mean abs-diff threshold")
    ap.add_argument("--period", type=float, default=2.0, help="seconds between samples")
    ap.add_argument("--entity-id", default=None, help="set when multiple doors/fridges share this source")
    args = ap.parse_args()
    x, y, w, h = (int(v) for v in args.roi.split(","))

    sl = ManySight(args.url, args.api_key)
    src = sl.source(args.source)
    sl.register_job(f"Fridge monitor – {src['name']}", "door state via ROI diff",
                    source_ids=[src["id"]], event_types=["state"])
    sl.register_worker("fridge-state", version="1")
    print("Contract: this worker sends a 'state' observation every sample period, "
          "including repeats — ManySight derives transitions and durations from them.")
    cap = sl.open_capture(src, args.connection)
    ok, ref = cap.read()
    if not ok:
        raise SystemExit("cannot read from source")
    gray = lambda f: cv2.cvtColor(f[y:y + h, x:x + w], cv2.COLOR_BGR2GRAY)  # noqa: E731
    ref_roi = gray(ref)

    state = "closed"
    last_heartbeat = 0.0
    print("monitoring… (reference frame assumes door is closed now)")

    while True:
        now = time.time()
        if now - last_heartbeat >= 10:
            command = sl.heartbeat(metrics={"state": state})
            last_heartbeat = now
            if command["should_stop"]:
                break
        ok, frame = cap.read()
        if not ok:
            break
        state = "open" if float(np.mean(cv2.absdiff(gray(frame), ref_roi))) > args.thresh else "closed"
        sl.submit_state(source_id=src["id"], name="door_state", label=state, entity_id=args.entity_id)
        sl.flush()
        time.sleep(args.period)
    sl.stop_worker()


if __name__ == "__main__":
    main()
