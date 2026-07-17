# State monitoring — is the fridge open? for how long?

Use when the user wants to monitor a **binary/enumerated state of equipment**:
fridge/freezer doors, lights, shutters, machine on/off — and see timelines, totals
(energy waste!) and duration alerts.

## What the platform needs from you

`state_change` events — **only when the state flips**:
- `label` = the new state ("open"/"closed")
- `source_id` set (timelines are per source); add `zone`/`zone_id` if a zone exists.
- Plus **one anchor event at startup** with the current state's label, so the
  timeline knows where it begins.

That's all. The platform derives every duration from consecutive `state_change`
timestamps — both for the States timeline and for duration alerts. Do not post
`value` or `attributes.prev_label`; they are ignored.

## Steps

1. `get_snapshot(source_id)` — look at the frame; pick the ROI (region of interest)
   around the door/indicator. Ask the user to confirm the ROI if ambiguous.
2. `register_job("Fridge monitor – <name>", event_types=["state_change"])`.
3. Classify state per sampled frame (1 frame / 2–5 s is plenty). Cheap and robust,
   in order of preference:
   - **ROI difference vs a reference "closed" frame** (template below) — grab the
     reference while the user confirms the door is closed;
   - edge density / brightness threshold in the ROI (open door ⇒ interior light);
   - a small classifier if accuracy demands it (depth estimation is possible but rarely needed).
4. Debounce (state must persist ~3 samples) then emit `state_change` per flip.
5. Optionally `create_alert_rule("Fridge left open", "state_alert", {"label":"open","min_seconds":120,"source_id":...})`
   — the platform fires it when the state *ends* after lasting that long, **and**
   while it is still ongoing past the threshold (as new events flow in).
6. Verify with `get_analytics("states", {})`.
7. Publish it: `register_insight("Fridge door states", block="state_timeline",
   dataset="states", params={"source_id": ...}, limitations="Durations derived from
   state_change timestamps; gaps read as the last known state.")`.

## Worker template

```python
import time, cv2, numpy as np, sys
sys.path.insert(0, "sdk/python")
from storelens import StoreLens

ROI = (x, y, w, h)                     # from step 1
THRESH = 18.0                          # mean abs-diff threshold; tune once
sl = StoreLens("http://localhost:8000")
src = sl.source(SOURCE_ID)
job = sl.register_job("Fridge monitor", "door open/closed via ROI diff", [src["id"]], ["state_change"])
cap = sl.open_capture(src)

ok, ref = cap.read()                   # user confirmed door is CLOSED now
ref_roi = cv2.cvtColor(ref[ROI[1]:ROI[1]+ROI[3], ROI[0]:ROI[0]+ROI[2]], cv2.COLOR_BGR2GRAY)
state, pending, pending_n = "closed", None, 0
sl.add_event(source_id=src["id"], event_type="state_change", label=state)  # anchor
sl.flush()

while True:
    ok, frame = cap.read()
    if not ok:
        break
    roi = cv2.cvtColor(frame[ROI[1]:ROI[1]+ROI[3], ROI[0]:ROI[0]+ROI[2]], cv2.COLOR_BGR2GRAY)
    observed = "open" if float(np.mean(cv2.absdiff(roi, ref_roi))) > THRESH else "closed"
    if observed != state:
        pending_n = pending_n + 1 if observed == pending else 1
        pending = observed
        if pending_n >= 3:             # debounce: 3 consecutive samples
            sl.add_event(source_id=src["id"], event_type="state_change", label=observed)
            sl.flush()
            state, pending_n = observed, 0
    else:
        pending_n = 0
    time.sleep(2)
```

## Pitfalls

- Emit on **change only** — a `state_change` every sample corrupts the timeline.
- Always send the initial state once at startup so the timeline has an anchor;
  without it the platform cannot time the first flip.
- `source_id` is required for duration alerts — the platform looks up the previous
  state per source.
- Lighting shifts move ROI-diff baselines — re-grab the reference frame when the user
  confirms "closed", or use edge density which is more lighting-tolerant.
