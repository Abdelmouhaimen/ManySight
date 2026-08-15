# Workspace reinitialization and space revisions

StoreLens separates spatial setup from observation history. Destructive actions require
exact confirmation text in Setup → Advanced → Danger zone and run in one SQLite
transaction.

## Reinitialize space

`POST /api/v1/workspace/reinitialize-space` starts a new `space_revision_id` and clears
active map geometry: plan, placements, calibrations, zones, camera views, projection
surfaces, multiview groups, and current derived state. Logical sources and protected
credentials remain.

Two history policies are available:

- `keep`: raw observations and historical fused/occupancy/alert evidence remain attached
  to the previous revision, whose geometry snapshot is archived. Current observation
  and analytics reads exclude those rows. Current materializations are cleared.
- `delete`: raw observations, materialized current/fused state, occupancy history, and
  fired alerts are also removed.

Zone database IDs are not reused. A saved query that references a deleted zone or group
returns `409 unresolved_query_reference`; StoreLens never silently applies it to a new
zone with the same name.

`GET /api/v1/workspace/revisions` lists revision metadata. Raw historical evidence can
be requested with `include_previous_space=true` or a specific `space_revision_id`.
Historical fused occupancy rows also carry a revision ID; time-series queries accept
`filters.space_revision_ids`. Queries that reference removed zone/group definitions stay
unresolved until deliberately repaired, even when old evidence is retained.

## Reinitialize observations

`POST /api/v1/workspace/reinitialize-observations` clears raw observations, source and
fused current materializations, fused/occupancy history, fired alerts, source counters,
and job counters. It preserves sources, credentials, plans, zones, calibrations,
multiview groups, saved queries, dashboards, and alert rules. Rule edge/cooldown state is
reset so retained rules evaluate only new evidence.

Neither operation is a substitute for a backup or retention policy.
