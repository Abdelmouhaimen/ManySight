---
name: generated-dashboard
description: Use when the user asks to show, save, pin, or build a dashboard view from StoreLens data. Creates declarative query-backed widgets, never custom React or raw SQL.
---

# Generated dashboards

1. Inspect existing dashboards and saved queries.
2. Load the `analytics` skill, preview the deterministic query, and reuse an equivalent
   saved query when one exists.
3. Create the saved query if necessary.
4. Create or update a dashboard.
5. Add a widget whose presentation matches the result shape: `number` for scalar,
   `timeseries` for time rows, `bar` for categorical/time rows, `table` for supported
   tabular results, or `heatmap` for spatial heatmap rows.
6. Verify the Dashboard route renders the value and quality.

Do not generate custom React code unless the generic renderer genuinely cannot support
an explicitly requested product extension. Changing presentation does not justify a new
query. Deleting a dashboard preserves queries and observations.
