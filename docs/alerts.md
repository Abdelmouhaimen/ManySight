# Alerts

Alerts evaluate StoreLens-derived state. Workers do not emit business alerts, occupancy,
zone entry, dwell, or transitions.

Legacy rule kinds remain available for dwell, occupancy, state, and event compatibility.
New generated workflows can use `query_condition`, which references a saved query and a
condition such as `{operator: ">=", value: 2, for_seconds: 0}`.

The guided demo uses this same path for its Aisle 04 threshold. Replay submits raw
source-local observations; the normal saved-query evaluator fires the Review record.
Demo rules and fired alerts remain isolated and are never included in setup promotion.

Query-backed alerts use the same deterministic engine as dashboard widgets. The evaluator:

- runs periodically, independent of observation ingestion;
- fires on the false-to-true edge and not repeatedly while the condition remains true;
- applies cooldown after a fired edge;
- resets only when known evidence makes the condition false;
- preserves active state when quality becomes unknown;
- uses partial quality only when `allow_partial` is explicitly enabled;
- includes value, threshold, quality, query ID, and held-since provenance in the payload.

Webhook URLs receive alert JSON and should be treated as sensitive deployment
configuration. StoreLens does not guarantee delivery retries beyond the current process
behavior; use a durable external workflow system when delivery guarantees matter.
