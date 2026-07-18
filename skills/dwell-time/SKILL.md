# Dwell time — how long do people stay in a zone?

Use when the user asks how long people spend somewhere ("dwell at checkout",
"queue time", "time at the promo stand"), optionally **split by an attribute**
("male vs female", "staff vs customer").

## What the platform needs from you

**Only `zone_enter`/`zone_exit` pairs** per (track, zone), with `attributes`
carrying any grouping keys. The platform derives every dwell duration from those
pairs — including visits still in progress.

Do **not** post `zone_dwell` events: they are deprecated. The platform stores them
as observations for backward compatibility but ignores their `value` in analytics
and alerts. Never compute dwell seconds yourself.

## Steps

1. `get_store_map()` / `list_zones()` — find the target zones. If the zone doesn't
   exist, ask the user to draw it (Store Map tab) **or** propose a polygon yourself from
   a frame captured locally by the worker and register it with `create_zone(name, ztype, polygon_px=...,
   source_id=...)` after user confirmation (the platform projects pixels to the map).
2. `register_job("Dwell – <zone/scope>", event_types=["detection","zone_enter","zone_exit"])`.
3. Detect + track people (see the `heatmap` skill for model options). For attribute
   splits (e.g. gender) run a lightweight classifier per track — classify a few crops,
   majority-vote, cache per `track_id`; put the result in `attributes`.
   *Note: appearance-based attributes are estimates — say so in the job description.*
4. Zone membership per track per frame: project feet pixels with `sl.project(src, pts)`
   (or let detections auto-zone and read back), then on membership change emit
   `zone_enter` / `zone_exit`. That's all — the platform does the timing.
5. Verify with `get_analytics("dwell", {"group_by": "<attr>"})` — the response is always
   derived (`"derived": true`) and reports `open_visits` for people still inside.
6. Publish it: `register_insight("Dwell by <attr> – <zone>", block="bar", dataset="dwell",
   params={"group_by": "<attr>"}, limitations="Derived from enter/exit pairs; appearance
   attributes are estimates.")` so the result appears in the Insights tab.

## Worker core (zone bookkeeping)

```python
import os, time, sys
sys.path.insert(0, "sdk/python")
from storelens import StoreLens, CentroidTracker

sl = StoreLens(os.environ["STORELENS_URL"])
src = sl.source(SOURCE_ID)
zones = sl.zones()
job = sl.register_job("Dwell by gender – Checkout", event_types=["zone_enter","zone_exit"])
inside = set()  # (track_id, zone_id) currently inside
attrs = {}      # track_id -> {"gender": ...} from your classifier

def on_track_position(tid, x_map, y_map, now):
    for z in zones:
        key = (tid, z["id"])
        member = sl.point_in_zone(z, x_map, y_map)
        if member and key not in inside:
            inside.add(key)
            sl.add_event(source_id=src["id"], event_type="zone_enter", track_id=tid,
                         zone_id=z["id"], attributes=attrs.get(tid, {}))
        elif not member and key in inside:
            inside.discard(key)
            sl.add_event(source_id=src["id"], event_type="zone_exit", track_id=tid,
                         zone_id=z["id"], attributes=attrs.get(tid, {}))
```

Feed it: `x_map, y_map = sl.project(src, [(feet_x, feet_y)])[0]` per tracked person.
On shutdown, flush open visits as `zone_exit` events so the platform can close them.

## Pitfalls

- `track_id` must be stable across frames or every dwell collapses to ~0 s.
- Emit enter/exit **on membership change only** — not continuously.
- Always close visits with a `zone_exit` (including at shutdown). An enter without an
  exit is counted as an in-progress visit and capped at one hour.
- Attribute keys become UI group-by options automatically (`dwell by gender` selector);
  keep values short and consistent (`female`/`male`, not free text). Attach them to the
  `zone_enter` (and ideally the exit too).
- Debounce membership (e.g. require 2–3 consecutive frames) to avoid flicker at polygon
  edges — a duplicate enter is ignored (first one wins), but flicker still creates
  spurious short visits.
