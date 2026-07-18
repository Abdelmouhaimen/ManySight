# Insights — publish what your analysis found

Use after any analysis (heatmap, dwell, states, counts) to make the result appear
in the dashboard's **Insights** catalogue — and whenever the user asks to "add",
"show", "pin", or "clean up" dashboard views. The Insights tab renders **registered
definitions only**; posting events alone shows nothing there.

## The model

An insight definition = a question + a visualization **block** + a platform
**dataset** + that dataset's query params. The dashboard renders it from a fixed
block registry — you register structure, never UI code.

| block | renders | datasets it accepts |
|---|---|---|
| `metric` | one KPI number | `summary` (params.field: tracks/events/active_tracks), `dwell` (avg for params.zone_id), `occupancy` (peak) |
| `line` | time series | `occupancy` (params.zone_id, label, group_by="label", event_type), `counts` (params.zone_id, label, aggregation="last" or "avg"; last is the default, and the headline always uses the latest raw sample) |
| `bar` | per-zone/per-group bars | `dwell` (params.zone_id?, group_by?) |
| `table` | rows | `dwell`, `transitions` |
| `heatmap_map` | floor-plan heatmap | `heatmap` (params.zone_id, label, source_id, job_id) |
| `flow_matrix` | zone→zone matrix | `transitions` |
| `state_timeline` | state totals per source | `states` (params.source_id?) |

## Steps

1. `list_insight_templates()` — see which combinations the current data supports
   (it scans count labels, state sources, zones, and attribute keys for you).
2. `list_insights()` — avoid registering a duplicate of an existing card.
3. `register_insight(title, block, dataset, params, question, unit, limitations, pinned)`.
   - A detection worker's top-level `label` is an observed class. Use
     `dataset="occupancy"` with `params={"event_type":"detection", "label":"child"}`
     for one class, or replace `label` with `group_by="label"` to compare classes.
     Add `zone_id` to scope either form to one zone.
   - Write `limitations` honestly — it is displayed on the card. Say what the metric
     cannot claim ("derived dwell, not validated wait time"; "appearance-based gender
     is an estimate").
   - `pinned=True` also shows the card on the Overview page — use sparingly.
4. Tell the user to open the Insights tab; the card queries live analytics with the
   user's selected time range.
5. Maintenance: `update_insight(id, {"status": "degraded"})` when its worker stops,
   `{"status": "retired"}` (or `delete_insight`) when it is obsolete.

## Pitfalls

- The block↔dataset pairing is validated server-side (422 on mismatch) — check the
  table above.
- `params` must be the dataset's real query params (zone_id, label, group_by,
  source_id...); unknown keys are ignored by the renderer.
- Don't register one card per zone by default — one bar/table card covers all zones;
  make per-zone cards only when the user asks about a specific zone.
