---
name: queries-dashboards-alerts
description: Use for any deterministic data question, generated dashboard, or alert rule. Covers the query/presentation/condition separation, fused occupancy semantics, exact comparison operators, and quality-aware edge triggering.
---

# Queries, dashboards, and alerts

Load [`storelens-core`](../storelens-core/SKILL.md) first.

```text
user intent
    ↓
saved query          the question — subject, measures, filters, grouping
    ↓
StoreLens result     deterministic, with quality and evidence window
    ↓
dashboard widget     presentation only
    ↓
alert condition      operator + value on the same query
```

**The query computes. A dashboard only presents.** A widget never calculates occupancy or
any other metric.

## Deterministic queries

1. `inspect_workspace()` for existing saved queries and `query_capabilities`.
2. `run_query(subject, measures, filters, grouping, range)` to preview.
3. Check the result `shape`, rows, `quality`, warnings, and metadata.
4. `configure_saved_query(...)` **once** for that question. Reuse or update rather than
   duplicating.
5. Only if the user wants it displayed: `configure_dashboard(...)`.

Subjects are `detection`, `measurement`, `state`, and `fused_entity`. Filters must use IDs
and vocabulary discovered from the platform, never guessed. `shape` says how to read rows
(`scalar`, `timeseries`, `categorical`, `heatmap`) — it never says which chart to draw.

Presentation is not part of a question's identity: wanting a different rendering, or a
different title, never justifies a second saved query. Patch the widget instead. Agents
never receive SQL and never generate dashboard code.

## "How many people are in Aisle 04?"

The correct semantics are **current fresh fused person entities inside the canonical zone**:

```text
subject   fused_entity
measures  ["current_occupancy"]
filters   {"group_ids": [g], "zone_ids": [z], "entity_types": ["person"]}
```

A scalar fused query needs exactly one group and one zone. A time-grouped fused query reads
persisted occupancy snapshots.

It is **not** a count of camera bounding boxes, **not** `DISTINCT` raw local tracker IDs
across cameras, and **not** frontend polygon membership.

## Dashboards

`configure_dashboard(name, widgets=[{query_id, title, presentation}])`. Match presentation
to the result shape: `number` for scalar, `timeseries` for time rows, `bar` for categorical,
`table` for tabular, `heatmap` for spatial heatmap rows. Verify the Dashboard route renders
the value and its quality. Deleting a dashboard preserves its queries and observations.

## Alerts — the operator is exact

Prefer `configure_alert(kind="query_condition", query_id=..., operator=..., value=...)` so
the alert evaluates exactly the saved query a dashboard shows.

Map the user's own words. These are **not** interchangeable, and StoreLens never converts
one into the other:

| the user says | operator |
|---|---|
| more than 2 / over 2 / above 2 | `>` with value 2 |
| at least 2 / 2 or more | `>=` with value 2 |
| fewer than 3 / less than 3 / under 3 | `<` with value 3 |
| at most 3 / no more than 3 / 3 or fewer | `<=` with value 3 |
| exactly 3 | `==` with value 3 |

"More than 2" fires at 3, not at 2. The guided demo's phrasing is "at least 2", which is
`>= 2` — do not copy its operator into a request that said "more than". If the phrasing is
not clearly one of the rows above, ask the user rather than guessing.

## Quality and edge triggering

The evaluator runs periodically and independently of ingestion, so a quiet zone or a stale
source is still caught. It fires only on the false-to-true edge, applies cooldown after
firing, and resets when **known** evidence makes the condition false.

- Unknown evidence does not false-clear an active condition.
- `partial` quality is ignored unless `allow_partial=true` is explicitly set — set it only
  when the user accepts partial camera coverage.
- An alert must never infer zero because a required camera went stale.

`for_seconds` requires the condition to hold continuously before firing. Configure a webhook
without exposing its URL in observations or logs.

Compatibility kinds (`dwell_exceeds`, `occupancy_exceeds`, `state_alert`, `event_match`,
`analysis_condition`) remain available through `params`. Do not create an `event_match` rule
for a business concept a worker should not be submitting in the first place.
