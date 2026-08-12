---
name: alerts-workflows
description: Use for threshold, dwell, state, event, fused-occupancy, and webhook alerts. Covers query-backed conditions, quality, periodic evaluation, and edge triggering.
---

# Alerts and workflows

Workers submit raw observations; StoreLens evaluates alert conditions centrally.

Prefer `query_condition` for a condition that should match a saved query or dashboard
metric:

1. Load `analytics`, inspect capabilities, and preview the query.
2. Reuse or create one saved query.
3. Create a rule with `params: {query_id}`, plus
   `condition: {operator, value, for_seconds?, window_s?, allow_partial?}`.
4. Verify current value, quality, threshold, and fired payload.
5. Configure an optional webhook without exposing its URL in observations or logs.

For the multiview acceptance example, save a scalar `fused_entity` query filtered to one
group, one zone, and `person`, then create `operator: ">=", value: 2`.

The evaluator runs periodically and independently of ingestion. It fires only on the
false-to-true edge, applies cooldown after firing, and resets when known evidence makes
the condition false. Unknown evidence does not false-clear an active condition. Partial
quality is ignored unless `allow_partial` is explicitly true.

Legacy `dwell_exceeds`, `occupancy_exceeds`, `state_alert`, `event_match`, and
`analysis_condition` remain available for compatibility. Do not create an event-match
rule for a business concept a worker should not submit.
