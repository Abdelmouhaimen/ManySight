# Alerts & workflows — notify when something happens

Use when the user wants to **be told** about conditions rather than (only) see charts:
loitering, crowding, queue too long, fridge left open, VIP/person-in-restricted-area —
and when they want events pushed to **external systems (n8n, Zapier, Slack webhooks)**.

## How alerting works here

Rules live server-side and are evaluated on **every ingested event batch** — so alerts
only fire if a worker is streaming the relevant events. An alert = UI toast + log entry
+ optional `POST webhook_url` with the alert JSON (n8n "Webhook" node consumes it as-is).

| kind | params | fires when |
|---|---|---|
| `dwell_exceeds` | `{zone_id?, seconds}` | a track's **platform-derived** dwell reaches `seconds` — either when its `zone_exit` closes a visit that long, or while it is still inside with no exit yet (ongoing loiter) |
| `occupancy_exceeds` | `{zone_id?, count, window_s}` | ≥ `count` distinct `track_id`s seen in the zone within the window |
| `state_alert` | `{label, source_id, min_seconds?}` | a `state_change` to `label` (immediate), or — with `min_seconds` — when the state lasted ≥ that long, **derived from consecutive `state_change` timestamps**; also fires while the state is still ongoing past the threshold. `source_id` is required for duration mode. |
| `event_match` | `{event_type, zone_id?, attr_key?, attr_value?}` | any matching event arrives |

Every rule: `cooldown_s` (default 60) throttles refiring; `webhook_url` optional.
Durations are never read from worker-posted values — post raw `zone_enter`/`zone_exit`
and label-only `state_change` events and let the platform do the timing. Ongoing
(loiter / still-open) checks only run when events are being ingested: a zone with zero
traffic re-alerts only when the next batch arrives.

## Steps

1. Make sure a worker streams the events the rule needs (dwell rule ⇒ dwell-time skill
   running; occupancy ⇒ any tracked detections/zone events; state ⇒ state-monitoring).
2. Map the user's sentence to a kind:
   - "someone hangs around X for more than N minutes" → `dwell_exceeds`
   - "more than N people at X" / "crowd forming" → `occupancy_exceeds` (window ≈ 30–120 s)
   - "fridge open more than 2 min" → `state_alert` with `min_seconds: 120`
   - "anyone enters the restricted area" → `event_match` `{event_type:"zone_enter", zone_id}`
     (if the zone doesn't exist yet, create it first from a locally captured frame with `create_zone` —
     the zone label carries no behavior; this rule is what makes it alert)
   - "any woman enters the stockroom" → `event_match` `{event_type:"zone_enter", zone_id, attr_key:"gender", attr_value:"female"}`
3. `create_alert_rule(name, kind, params, webhook_url?, cooldown_s?)` — resolve zone
   names to ids via `list_zones()` first.
4. Test it: post synthetic matching events via `submit_events` and confirm the alert
   appears (`GET /api/v1/alerts`) and the webhook received the POST. For a dwell rule
   post a `zone_enter` backdated by more than `seconds` and then a `zone_exit` now
   (same track and zone); for a state duration rule post the `label` state_change
   backdated, then the closing flip. Tell the user you tested with synthetic data.

## Webhook payload (what n8n receives)

```json
{
  "id": 12, "rule_id": 3, "rule_name": "Loitering at checkout",
  "ts": 1789456123.4, "title": "Loitering at checkout",
  "message": "Track t42 dwelled 145s in Checkout (limit 120s)",
  "payload": { "event": { "...the triggering event..." } },
  "acknowledged": 0
}
```

n8n side: Webhook node (POST) → any downstream (Slack, email, sheets). No auth is sent;
use an unguessable webhook path or an n8n header-auth webhook + a reverse proxy if needed.

## Pitfalls

- A rule without a running worker never fires — always check jobs are active first.
- `occupancy_exceeds` needs **stable track ids**; blob trackers that reassign ids inflate counts.
- Set `cooldown_s` realistically (loitering: 300 s+) or the log fills with duplicates.
