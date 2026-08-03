---
name: state-observation
description: Use when the user wants to monitor a binary/enumerated state of equipment or a scene — fridge/freezer doors, lights, shutters, machine on/off — and see timelines, totals (energy waste!), and duration alerts. Covers submitting repeated state samples; StoreLens derives transitions and durations itself.
---

# State observation — is the fridge open? for how long?

## What the platform needs from you

`state` observations — sent **on every sample, including runs of identical values**:
- `name` = the state key (e.g. `"door_state"`) — required.
- `label` = the observed value now (e.g. `"open"`/`"closed"`) — required.
- `source_id` set (timelines are keyed per source/name/entity).
- `entity_id` when more than one independently stateful thing shares a source and name
  (e.g. two fridges on one camera) — otherwise omit it.

That's all. Do **not** try to detect a flip yourself and only send on change — StoreLens
coalesces consecutive identical samples into intervals and derives every duration,
transition, and duration alert from them. Sending `state_change` (only-on-flip events)
is the retired contract; `submit_observations` rejects it with `legacy_derived_observation`.
Do not post a computed duration in `info`/`attributes`; it is ignored.

## Steps

1. Capture a frame on the worker device — inspect it and pick the ROI (region of interest)
   around the door/indicator. Ask the user to confirm the ROI if ambiguous.
2. `register_job("Fridge monitor – <name>", event_types=["state"])`.
3. Classify state per sampled frame (1 frame / 2–5 s is plenty). Cheap and robust,
   in order of preference:
   - **ROI difference vs a reference "closed" frame** (template below) — grab the
     reference while the user confirms the door is closed;
   - edge density / brightness threshold in the ROI (open door ⇒ interior light);
   - a small classifier if accuracy demands it.
4. Submit a `state` observation every sample period, regardless of whether the value
   changed since last time.
5. Optionally `create_alert_rule("Fridge left open", "state_alert",
   {"label":"open","name":"door_state","min_seconds":120,"source_id":...})` — evaluated
   on a periodic timer (every ~15s), so it fires both right after the state ends and
   while it's still ongoing past the threshold, without needing another observation to
   arrive.
6. Verify with `query_analytics("state", ["current","duration"], filters={"source_ids":[...]})`.
7. Publish it: `create_analysis(name="Fridge door states", subject="state",
   measures=["duration","time_percentage"], filters={"source_ids":[...]},
   presentation="state_timeline", question="How long does the fridge stay open?")`.

## Worker template

```python
import os, sys
sys.path.insert(0, "sdk/python")
from storelens import StoreLens
import cv2, numpy as np, time

ROI = (x, y, w, h)                     # from step 1
THRESH = 18.0                          # mean abs-diff threshold; tune once
sl = StoreLens(os.environ["STORELENS_URL"])
src = sl.source(SOURCE_ID)
job = sl.register_job("Fridge monitor", "door state via ROI diff", [src["id"]], ["state"])
sl.register_worker("fridge-state", version="1")
cap = sl.open_capture(src)

ok, ref = cap.read()                   # user confirmed door is CLOSED now
ref_roi = cv2.cvtColor(ref[ROI[1]:ROI[1]+ROI[3], ROI[0]:ROI[0]+ROI[2]], cv2.COLOR_BGR2GRAY)

while True:
    ok, frame = cap.read()
    if not ok:
        break
    roi = cv2.cvtColor(frame[ROI[1]:ROI[1]+ROI[3], ROI[0]:ROI[0]+ROI[2]], cv2.COLOR_BGR2GRAY)
    state = "open" if float(np.mean(cv2.absdiff(roi, ref_roi))) > THRESH else "closed"
    sl.submit_state(source_id=src["id"], name="door_state", label=state)  # every sample
    sl.flush()
    time.sleep(2)
```

## Pitfalls

- Send **every** sample, not just changes — a worker that only sends on flip is using the
  retired contract and gets rejected. Light debouncing to avoid frame-level flicker is
  still good practice, but it's a worker-quality concern, not a contract requirement.
- `source_id` (+ `entity_id` if there's more than one stateful thing) is required —
  StoreLens looks up the previous coalesced interval per that key.
- A source that stops reporting doesn't keep "counting" forever: once the most recent
  sample is older than the staleness timeout, StoreLens reports the state as stale rather
  than extending its duration — don't rely on a single startup anchor the way the old
  `state_change` contract required.
- Lighting shifts move ROI-diff baselines — re-grab the reference frame when the user
  confirms "closed", or use edge density which is more lighting-tolerant.
