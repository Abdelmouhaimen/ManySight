# State monitoring — is the fridge open? for how long?

Use when the user wants to monitor a **binary/enumerated state of equipment**:
fridge/freezer doors, lights, shutters, machine on/off — and see timelines, totals
(energy waste!) and duration alerts.

## What the platform needs from you

`state_change` events — **only when the state flips**:
- `label` = the new state ("open"/"closed")
- `value` = duration in seconds of the state that just **ended**
- `attributes.prev_label` = the state that just ended
- `source_id` set (timelines are per source); add `zone`/`zone_id` if a zone exists.

The Insights "States" card renders the timeline + totals per label. Duration alerts
(`state_alert` with `min_seconds`) key off `value`/`prev_label` on the closing event.

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
5. Optionally `create_alert_rule("Fridge left open", "state_alert", {"label":"open","min_seconds":120,"source_id":...})`.
6. Verify with `get_analytics("states", {})`.

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
state, state_since, pending, pending_n = "closed", time.time(), None, 0

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
            now = time.time()
            sl.add_event(source_id=src["id"], event_type="state_change", label=observed,
                         value=now - state_since, attributes={"prev_label": state})
            sl.flush()
            state, state_since, pending_n = observed, now, 0
    else:
        pending_n = 0
    time.sleep(2)
```

## Pitfalls

- Emit on **change only** — a `state_change` every sample corrupts the timeline.
- Always send the initial state once at startup (`value=0`, `prev_label=state`) so the
  timeline has an anchor.
- Lighting shifts move ROI-diff baselines — re-grab the reference frame when the user
  confirms "closed", or use edge density which is more lighting-tolerant.
