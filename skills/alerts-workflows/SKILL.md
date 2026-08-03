---
name: alerts-workflows
description: Use when the user wants to be told about conditions rather than (only) see charts — loitering, crowding, queue too long, fridge left open, VIP/person-in-restricted-area — and when they want events pushed to external systems (n8n, Zapier, Slack webhooks).
---

# Alerts & workflows — notify when something happens

## How alerting works here

Rules live server-side. Completed conditions (a dwell that just closed, a matched
observation, a state that just changed) are checked right after each ingested batch.
**Ongoing/time-based conditions — loitering, occupancy still over threshold, a state
still stuck, or any `analysis_condition` — are also checked on a periodic timer
(roughly every 15s), independent of ingestion.** A quiet zone or a stale source still
gets caught; you do not need to keep a worker streaming just to re-trigger a check.
An alert = UI toast + log entry + optional `POST webhook_url` with the alert JSON
(n8n's "Webhook" node consumes it as-is).

| kind | shape | fires when |
|---|---|---|
| `dwell_exceeds` | `params: {zone_id?, seconds}` | an entity's platform-derived dwell (tracked detections, or legacy enter/exit pairing) reaches `seconds` |
| `occupancy_exceeds` | `params: {zone_id?, count, window_s}` | ≥ `count` distinct entities seen in the zone within the window |
| `state_alert` | `params: {label, name?, entity_id?, source_id, min_seconds?}` | a state samples to `label` (immediate, if `min_seconds` omitted), or the coalesced interval in `label` has lasted ≥ `min_seconds` — including while still ongoing |
| `event_match` | `params: {event_type, zone_id?, attr_key?, attr_value?}` | any matching observation arrives |
| `analysis_condition` | `analysis: {subject, measures, filters}`, `condition: {operator, value, for_seconds?, window_s?}` | the general case — the named measure (evaluated as a KPI over the trailing `window_s`, default 300s) satisfies `operator`/`value` continuously for `for_seconds` (default 0 = immediately) |

Every rule: `cooldown_s` (default 60) throttles refiring; `webhook_url` optional.
Durations and counts are never read from worker-posted values — submit raw `detection`/
`state` observations and let the platform do the timing.

## Steps

1. Make sure a worker is submitting the observations the rule needs (dwell/occupancy ⇒
   `detection-tracking`; state ⇒ `state-observation`; a general threshold ⇒ whatever
   subject the `analysis_condition` reads).
2. Map the user's sentence to a kind:
   - "someone hangs around X for more than N minutes" → `dwell_exceeds`
   - "more than N people at X" / "crowd forming" → `occupancy_exceeds` (window ≈ 30–120 s)
   - "fridge open more than 2 min" → `state_alert` with `min_seconds: 120`
   - "anyone enters the restricted area" → create the zone first (see
     `geometry-calibration`), then `event_match {event_type:"detection", zone_id}` — the
     zone's label carries no behavior; this rule is what makes it alert
   - "queue length over 10 for 5 minutes" / any measurement or KPI threshold not covered
     above → `analysis_condition` with `analysis: {subject:"measurement",
     measures:["latest"], filters:{measurement_names:["queue_length"]}},
     condition: {operator:">", value:10, for_seconds:300}`
3. `create_alert_rule(name, kind, params?, analysis?, condition?, webhook_url?, cooldown_s?)`
   — resolve zone names to ids via `list_zones()` first.
4. Test it: submit synthetic matching observations via `submit_observations` and confirm
   the alert appears (`GET /api/v1/alerts`) and the webhook received the POST. Tell the
   user you tested with synthetic data.

## Webhook payload (what n8n receives)

```json
{
  "id": 12, "rule_id": 3, "rule_name": "Loitering at checkout",
  "ts": 1789456123.4, "title": "Loitering at checkout",
  "message": "Track t42 has been in Checkout for 145s and counting (limit 120s)",
  "payload": { "open_visit": { "...derived visit fields..." } },
  "acknowledged": 0
}
```

n8n side: Webhook node (POST) → any downstream (Slack, email, sheets). No auth is sent;
use an unguessable webhook path or an n8n header-auth webhook + a reverse proxy if needed.

## Pitfalls

- `occupancy_exceeds` needs **stable entity ids**; blob trackers that reassign ids inflate counts.
- Set `cooldown_s` realistically (loitering: 300 s+) or the log fills with duplicates.
- `analysis_condition`'s `for_seconds` is tracked across polls on the rule itself — if the
  condition stops holding even briefly, the timer resets; don't set `for_seconds` shorter
  than a couple of poll intervals or it will effectively behave like `for_seconds: 0`.
