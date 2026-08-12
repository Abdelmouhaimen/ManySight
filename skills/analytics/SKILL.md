---
name: analytics
description: Use when answering a deterministic data question without exposing SQL. Covers query capabilities, previews, filters, grouping, current fused occupancy, and saved-query reuse.
---

# Deterministic queries

A query is a data question, not a chart. It contains a subject, measures, filters,
grouping, range, and optional comparison. StoreLens evaluates it; never download the
database or calculate platform-owned occupancy yourself.

1. Inspect current sources, zones, multiview quality, and `list_saved_queries()`.
2. Call `list_query_capabilities()` before selecting a subject or measure.
3. Preview the exact definition with `query_data(...)`.
4. Check the result shape, rows, quality, warnings, and provenance.
5. Use `create_saved_query(...)` once. Reuse or update it rather than duplicating a
   question for another presentation.
6. Load `generated-dashboard` only if the user wants the query displayed.

Subjects are `detection`, `measurement`, `state`, and `fused_entity`. A scalar fused
occupancy query needs exactly one `group_id`, one `zone_id`, and an entity type. A
time-grouped fused query reads persisted occupancy snapshots. Unknown quality is not zero.

Query filters must use IDs and vocabulary discovered from the platform. Presentation is
cosmetic and belongs to a dashboard widget. Historical `analysis` terminology is a
compatibility implementation detail; use saved-query tools for current work.
