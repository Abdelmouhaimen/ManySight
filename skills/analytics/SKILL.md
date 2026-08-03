---
name: analytics
description: Use after any analysis (detection tracking, measurement, or state observation) to make the result appear on the dashboard, and whenever the user asks to "add", "show", "pin", "save", or "clean up" dashboard views. The Analytics page renders only saved analyses; submitting observations alone shows nothing there except live current-value cards.
---

# Analytics — publish what your analysis found

A saved analysis is a **data question** — subject, measures, filters, grouping — never
a chart definition. The dashboard renders it from a fixed result shape; you save
structure, never UI code, and switching how it's displayed later is a patch on the same
record, not a second one.

## The model

| field | meaning |
|---|---|
| `subject` | `detection` \| `measurement` \| `state` |
| `measures` | what to compute — see `list_analysis_capabilities()` for the valid set per subject (e.g. detection: `active_entities`, `distinct_entities`, `observations`, `visits`, `average_dwell`, `total_dwell`, `transition_count`, `density`; measurement: `latest`, `minimum`, `maximum`, `average`, `sum`, `rate`, `samples`; state: `current`, `changes`, `duration`, `average_duration`, `time_percentage`) |
| `filters` | `source_ids`, `zone_ids`, `labels`, `entity_types`, `entity_ids`, `attributes:{key:value}`, plus subject-specific `measurement_names`/`state_names`/`state_labels` |
| `grouping` | `primary`: none (KPI) \| `time` (with a `bucket` like `"5m"`) \| `zone`; optional `split_by`: `label`, `entity_type`, `entity_id`, `source`, `state_label`, `measurement_name`, or `attribute:<key>` |
| `presentation` | an optional renderer hint (`heatmap_map`, `flow_matrix`, `state_timeline`, `bar`, `table`, `line`) — cosmetic only |

## Steps

1. `list_analysis_capabilities()` — see which subjects/measures/groupings the current
   data actually supports (it scans labels, sources, zones, measurement/state names, and
   attribute keys for you).
2. `list_analyses()` — avoid registering a duplicate of an existing question. Creating an
   analysis with the same (subject, measures, filters, grouping) as an existing one is
   flagged via `duplicate_of` in the response rather than silently allowed to diverge.
3. `create_analysis(name, subject, measures, filters, grouping, question, presentation, pinned)`.
   - Write `question` honestly — it's shown with the card. Say what the metric cannot
     claim ("derived dwell, not validated wait time"; "appearance-based gender is an estimate").
   - `pinned=True` also shows the card on the Dashboard page — use sparingly.
   - Never create two analyses for the same question just to show it as both a bar chart
     and a table — that's a `presentation` choice on one record.
4. Tell the user to open the Analytics page; the card queries live analytics against the
   user's selected time range.
5. Maintenance: `update_analysis(id, {"status": "degraded"})` when its worker stops,
   `{"status": "retired"}` (or `delete_analysis`) when it is obsolete.

## Pitfalls

- Measures are validated against the subject server-side (422 on mismatch) — check
  `list_analysis_capabilities()`'s `measures_by_subject`.
- `filters`/`grouping` must use the real vocabulary above; unknown keys are ignored by
  the query engine, not treated as an error, so a typo silently does nothing — double
  check with a `query_analytics` preview before saving.
- Don't register one card per zone by default — one zone-grouped card covers all zones;
  make per-zone cards only when the user asks about a specific zone.
- `register_insight`/the old block+dataset+params model is retired. If you're extending
  an older StoreLens integration that still calls it, it's kept only as a best-effort
  compatibility adapter onto `create_analysis` — migrate the caller, don't rely on it.
