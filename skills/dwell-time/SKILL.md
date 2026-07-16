# Dwell time — how long do people stay in a zone?

Use when the user asks how long people spend somewhere ("dwell at checkout",
"queue time", "time at the promo stand"), optionally **split by an attribute**
("male vs female", "staff vs customer").

## What the platform needs from you

Best: explicit `zone_dwell` events — one per completed visit, `value` = seconds,
`zone_id`/`zone` set, `attributes` carrying any grouping keys. (Also send
`zone_enter`/`zone_exit` so flow and occupancy work; the platform can derive dwell
from those pairs if you skip `zone_dwell`.)

## Steps

1. `get_store_map()` / `list_zones()` — find the target zones. If the zone doesn't
   exist, ask the user to draw it (Store Map tab) **or** propose a polygon yourself from
   a snapshot and create it via `POST /zones` after user confirmation.
2. `register_job("Dwell – <zone/scope>", event_types=["detection","zone_enter","zone_exit","zone_dwell"])`.
3. Detect + track people (see the `heatmap` skill for model options). For attribute
   splits (e.g. gender) run a lightweight classifier per track — classify a few crops,
   majority-vote, cache per `track_id`; put the result in `attributes`.
   *Note: appearance-based attributes are estimates — say so in the job description.*
4. Zone membership per track per frame: project feet pixels with `sl.project(src, pts)`
   (or let detections auto-zone and read back), then on membership change emit
   `zone_enter` / `zone_exit`, and on exit a `zone_dwell` with the elapsed seconds.
5. Verify with `get_analytics("dwell", {"group_by": "<attr>"})`.

## Worker core (zone bookkeeping)

```python
import time, sys
sys.path.insert(0, "sdk/python")
from storelens import StoreLens, CentroidTracker

sl = StoreLens("http://localhost:8000")
src = sl.source(SOURCE_ID)
zones = sl.zones()
job = sl.register_job("Dwell by gender – Checkout", event_types=["zone_enter","zone_exit","zone_dwell"])
inside = {}   # (track_id, zone_id) -> enter_ts
attrs = {}    # track_id -> {"gender": ...} from your classifier

def on_track_position(tid, x_map, y_map, now):
    for z in zones:
        key = (tid, z["id"])
        member = sl.point_in_zone(z, x_map, y_map)
        if member and key not in inside:
            inside[key] = now
            sl.add_event(source_id=src["id"], event_type="zone_enter", track_id=tid,
                         zone_id=z["id"], attributes=attrs.get(tid, {}))
        elif not member and key in inside:
            t0 = inside.pop(key)
            sl.add_event(source_id=src["id"], event_type="zone_exit", track_id=tid,
                         zone_id=z["id"], attributes=attrs.get(tid, {}))
            sl.add_event(source_id=src["id"], event_type="zone_dwell", track_id=tid,
                         zone_id=z["id"], value=now - t0, attributes=attrs.get(tid, {}))
```

Feed it: `x_map, y_map = sl.project(src, [(feet_x, feet_y)])[0]` per tracked person.
On shutdown, flush open visits as `zone_dwell` too.

## Pitfalls

- `track_id` must be stable across frames or every dwell collapses to ~0 s.
- Emit dwell **on exit**, not continuously — one event per visit.
- Attribute keys become UI group-by options automatically (`dwell by gender` selector);
  keep values short and consistent (`female`/`male`, not free text).
- Debounce membership (e.g. require 2–3 consecutive frames) to avoid flicker at polygon edges.
