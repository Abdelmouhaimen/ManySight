---
name: measurement
description: Use for any numeric reading over time that isn't a tracked-entity presence question — population counts from a classifier ("children in the hall", "vehicles in the lot"), queue length, occupied-desk count, or any other model output that is a number, not a position or a state label. Covers submitting raw measurement samples; StoreLens aggregates them itself.
---

# Measurement — a numeric reading over time

Use when the answer is a **number that changes over time** and isn't naturally a tracked
entity (that's `detection-tracking`) or a categorical value (that's `state-observation`).
Classic cases: "how many children are in the hall right now", "queue length at checkout",
"how many desks are occupied".

## What the platform needs from you

`measurement` observations — one per sampling interval, never a precomputed average or a
time-aggregated total:
- `name` = the metric (e.g. `"children_present"`, `"queue_length"`) — required.
- `value` — required, a directly observed number.
- `value_kind`: `gauge` (default — an instantaneous sampled value, e.g. people currently
  waiting), `delta` (an increment observed this sample, e.g. three new entries), or
  `cumulative` (a monotonically increasing producer counter — StoreLens detects resets so
  a worker restart never produces a negative rate). Get this right; aggregation depends on it.
- `label` optionally qualifies which instance (e.g. `"checkout_queue"` when there are
  several queues) — it is not "what is measured" (that's `name`).
- `entity_id` + `geometry` are both optional: a measurement is only zone-assigned if it
  carries geometry (e.g. `point_map` at the relevant spot) or shares an `entity_id` with a
  recent detection. A population count with no natural single point usually needs a
  `point_map` hint if the user wants it zone-filtered.

## Steps

1. `register_job("<question> – <scope>", event_types=["measurement"], source_ids=[...])`.
2. Run the classifier/counter per sampling interval (typically every few seconds to a
   minute — match the rate the underlying model can actually support meaningfully).
3. Decide `value_kind` from what the model actually produces: most classifier counts are
   `gauge`. A "new arrivals this interval" tally is `delta`. A hardware/producer counter
   that only increases is `cumulative`.
4. Submit one `measurement` per interval — never average client-side and never post a
   running total when `value_kind="gauge"` is what's meant.
5. Verify with `query_analytics("measurement", ["latest","average"],
   filters={"measurement_names":["<name>"]}, grouping={"primary":"time","bucket":"5m"})`.
6. Publish it: `create_analysis(name, subject="measurement", measures=["latest","average"],
   filters={"measurement_names":["<name>"]}, grouping={"primary":"time","bucket":"..."},
   presentation="line")`.

## Worker template

```python
import os, sys, time
sys.path.insert(0, "sdk/python")
from storelens import StoreLens

sl = StoreLens(os.environ["STORELENS_URL"])
src = sl.source(SOURCE_ID)
job = sl.register_job("Hall population", "classifier-based population count",
                      source_ids=[src["id"]], event_types=["measurement"])
sl.register_worker("hall-population", version="1")

while True:
    count = run_classifier(read_frame())  # your model
    sl.submit_measurement(source_id=src["id"], name="children_present", value=count,
                          value_kind="gauge", attributes={"model": "your-model-v1"})
    sl.flush()
    time.sleep(60)
```

## Pitfalls

- Don't conflate `name` and `label` — `name` is the metric identity (what Analytics
  groups/filters by as `measurement_names`); `label` is a secondary qualifier, often unset.
- Never sum `gauge` samples to get a "total" — that's meaningless for an instantaneous
  reading. If the user wants a total, they mean `delta`/`cumulative` semantics; confirm
  which before choosing `value_kind`.
- A `cumulative` counter's rate is derived from consecutive increases only; a worker
  restart that resets the counter to zero doesn't need special handling on the worker
  side — the platform detects the reset and never reports a negative rate.
- If the user wants this filtered by zone and the measurement has no natural position,
  give it a `point_map` (e.g. the zone's centroid) — otherwise it simply won't show up in
  a zone-scoped analysis, by design, rather than being silently miszoned.
