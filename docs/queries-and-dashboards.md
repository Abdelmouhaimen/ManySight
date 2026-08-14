# Saved queries and generated dashboards

StoreLens exposes deterministic analytics instead of arbitrary SQL. A saved query is the
canonical analytical question: subject, measures, filters, grouping, range, and optional
comparison. Presentation is not part of query identity.

Supported subjects include raw `detection`, `measurement`, `state`, and current/historical
`fused_entity` state. Inspect `GET /api/v1/queries/capabilities` before composing a query,
then preview it through `POST /api/v1/analytics/query`.

Fused results include an `evidence_window` in response metadata. It states whether the
value comes from current complete samples or persisted occupancy history, alongside the
requested `since`/`until` range. Query references are ID-based: a deleted zone or group
is reported as unresolved rather than matching a later object with the same name.

A generated dashboard contains metadata plus widgets. Every widget references one saved
query and one validated presentation:

- `number` for scalar results;
- `timeseries` for time-grouped results;
- `bar` for categorical or time results;
- `table` for any supported result shape;
- `heatmap` for spatial heatmap rows.

Agents create these declarations through MCP or REST. They do not generate React code or
read the SQLite database. The bundled Dashboard page is a generic renderer and shows a
clear empty state when no generated dashboard exists. Multiple dashboards can be selected.

Deleting a widget or dashboard does not delete observations or saved queries. A saved
query referenced by a widget cannot be deleted until the reference is removed. Historical
`insight_definitions` and `analyses` rows are migrated into the saved-query store where
possible, but Insights is not a current product page or MCP workflow.

## Typical agent workflow

1. `inspect_workspace` — sources, geometry, current quality, existing saved queries, and
   the trimmed query-capability block.
2. `run_query` to preview the exact definition.
3. `configure_saved_query` once for that question; reuse an equivalent definition rather
   than duplicating it for a different presentation or wording.
4. `configure_dashboard` only when the user asked to see something, with a presentation
   compatible with the result shape.
5. Verify the rendered result and provenance.

See [the agent operating surface](agent-surface.md) for the curated tool list, and
[`queries-dashboards-alerts`](../skills/queries-dashboards-alerts/SKILL.md) for the full
playbook including the exact comparison-operator table.

## Threshold phrasing is exact

An occupancy question and its threshold are separate decisions, and English threshold
words map to exactly one operator. StoreLens never normalizes one into another:

| the user says | operator |
|---|---|
| more than 2 / over 2 / above 2 | `>` with value 2 |
| at least 2 / 2 or more | `>=` with value 2 |
| fewer than 3 / less than 3 / under 3 | `<` with value 3 |
| at most 3 / no more than 3 / 3 or fewer | `<=` with value 3 |
| exactly 3 | `==` with value 3 |

"More than 2" fires at 3, not at 2. The guided demo's own phrasing is "at least 2", which
is `>= 2`; do not copy its operator into a differently worded request. The table is
published at `GET /api/v1/agent/workflows/create-zone-occupancy-alert` so an agent can
look it up instead of guessing, and an unlisted phrasing should reach the user as a
question.
