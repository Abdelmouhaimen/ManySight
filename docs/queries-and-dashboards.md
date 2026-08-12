# Saved queries and generated dashboards

StoreLens exposes deterministic analytics instead of arbitrary SQL. A saved query is the
canonical analytical question: subject, measures, filters, grouping, range, and optional
comparison. Presentation is not part of query identity.

Supported subjects include raw `detection`, `measurement`, `state`, and current/historical
`fused_entity` state. Inspect `GET /api/v1/queries/capabilities` before composing a query,
then preview it through `POST /api/v1/analytics/query`.

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

1. Inspect sources, geometry, current quality, and existing saved queries.
2. Call `list_query_capabilities` and preview with `query_data`.
3. Create or update one saved query for the question.
4. Create a dashboard if needed.
5. Add a widget using a presentation compatible with the query result shape.
6. Verify the rendered result and provenance.
