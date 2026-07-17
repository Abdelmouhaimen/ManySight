"""Fridge/door state worker — open/closed timeline from a real video source.

Compares a region of interest against a reference frame captured at startup
(door must be CLOSED when the script starts). Emits `state_change` with the new
state's label on flips (plus one anchor at startup); the platform derives
durations and duration alerts from consecutive timestamps.

Usage:
    python examples/fridge_state.py --source 2 --roi 100,80,220,300 [--thresh 18]
"""
import sys
import time

sys.path.insert(0, "sdk/python")
from storelens import StoreLens, parse_args_base  # noqa: E402


def main():
    import cv2
    import numpy as np

    ap = parse_args_base(__doc__)
    ap.add_argument("--roi", required=True, help="x,y,w,h in pixels around the door")
    ap.add_argument("--thresh", type=float, default=18.0, help="mean abs-diff threshold")
    ap.add_argument("--period", type=float, default=2.0, help="seconds between samples")
    args = ap.parse_args()
    x, y, w, h = (int(v) for v in args.roi.split(","))

    sl = StoreLens(args.url, args.api_key)
    src = sl.source(args.source)
    sl.register_job(f"Fridge monitor – {src['name']}", "door state via ROI diff",
                    source_ids=[src["id"]], event_types=["state_change"])
    cap = sl.open_capture(src)
    ok, ref = cap.read()
    if not ok:
        raise SystemExit("cannot read from source")
    gray = lambda f: cv2.cvtColor(f[y:y + h, x:x + w], cv2.COLOR_BGR2GRAY)  # noqa: E731
    ref_roi = gray(ref)

    state, since = "closed", time.time()
    sl.add_event(source_id=src["id"], event_type="state_change", label=state)
    sl.flush()
    pending, pending_n = None, 0
    print("monitoring… (reference frame assumes door is closed now)")

    while True:
        ok, frame = cap.read()
        if not ok:
            break
        observed = "open" if float(np.mean(cv2.absdiff(gray(frame), ref_roi))) > args.thresh else "closed"
        if observed != state:
            pending_n = pending_n + 1 if observed == pending else 1
            pending = observed
            if pending_n >= 3:
                now = time.time()
                sl.add_event(source_id=src["id"], event_type="state_change", label=observed)
                sl.flush()
                print(f"{state} → {observed} after {now - since:.0f}s")
                state, since, pending_n = observed, now, 0
        else:
            pending_n = 0
        time.sleep(args.period)


if __name__ == "__main__":
    main()
