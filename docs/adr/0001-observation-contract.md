# ADR 0001: Observe locally, derive centrally

Date: 2026-08-03
Status: implemented (this redesign)

## Context

The previous contract asked workers to submit both raw evidence *and* several
platform-shaped derived events: `zone_enter`/`zone_exit` pairs (a business event,
not a direct observation), label-only `state_change` flips (requiring the worker
to detect its own transitions), and `count` samples. Each of these pushed a piece
of the platform's job — zone resolution, change detection, aggregation — onto
every worker author, which meant:

- Every new worker re-implemented zone-boundary hysteresis, enter/exit pairing, and
  change-detection debouncing, with inconsistent quality.
- The platform could not correct or improve derivation logic retroactively — a
  worker's enter/exit pairing decisions were baked into the stored event stream.
- The insight model (block + dataset + params) conflated a data question with its
  visualization, so switching a chart type meant a second insight definition, and
  "what data is this insight backed by" required cross-referencing dataset/param
  documentation per block type.

## Decision

**Workers submit only three direct observation kinds — `detection`, `measurement`,
`state` — and StoreLens derives everything else**: zone assignment, visits, dwell,
occupancy, movement between zones, state transitions and durations, aggregations,
every analysis, and every alert condition. A worker that tries to submit a
zone-resolved or platform-derived kind is rejected outright
(`legacy_derived_observation`), not silently reinterpreted.

Concretely, this repo's implementation:

- Extends the existing `events` table additively (schema_version, observation_id
  idempotency key, worker_id, name, entity_type, value_kind, unit, confidence,
  identity_scope, identity_model_version) rather than introducing a parallel
  table — `track_id` doubles as the API's `entity_id`, and `event_type` holds
  either a legacy kind (schema_version=1) or the new `kind` (schema_version=2).
  One storage model, two generations of writer.
- Shares one enrichment pipeline (`services/enrich.py`) between the legacy
  `/events` endpoint and the new `/observations/batch` endpoint — there is no
  second, parallel geometry/projection implementation.
- Adds detection-based visit derivation (`derive_visits_from_detections`) that
  groups ordered, zone-assigned detections per entity into sessions with gap
  tolerance and a minimum-confirmed-samples rule, and merges it with the legacy
  enter/exit-pair derivation (`derive_visits`) so historical and current data
  both contribute to the same dwell/occupancy/transition numbers.
- Adds state-sample coalescing (`coalesce_state_intervals`) so a worker can (and
  should) submit a `state` sample on every reading, including long runs of
  identical values, without inflating the transition count — and marks a series
  stale once it stops reporting, rather than treating the last known value as
  permanently current.
- Replaces the block+dataset+params insight model with a saved **analysis**:
  `{subject, measures, filters, grouping}` is the analytical identity;
  `presentation` is a cosmetic hint. Changing how a result is displayed patches
  the same record; it never creates a second one (enforced via a normalized
  query hash, `db.py:analysis_hash`).
- Fixes ongoing/time-based alert evaluation to run on a periodic timer
  (`server/app.py`'s lifespan-managed `_alert_poll_loop`, calling
  `alert_engine.evaluate_ongoing`) independent of ingestion — the previous
  design only re-checked a loitering/over-capacity/stuck-state condition when
  another event happened to land in the same zone, so a quiet zone could loiter
  undetected indefinitely.

## Why visualization is separated from analytical identity

A chart type is a rendering decision about a result shape (`scalar`/`timeseries`/
`categorical`/`heatmap`), not part of the question being asked. Coupling them
meant "dwell by zone as a bar chart" and "dwell by zone as a table" were two
insight definitions answering the same question — indistinguishable analytically,
duplicated administratively. The unified query engine
(`server/routers/analytics_query.py`) returns a typed, self-describing result;
the frontend picks a renderer from `shape` and `dimensions`, never from anything
stored on the analysis.

## Why opaque entity IDs do not equal human identity

`entity_id` is whatever a worker's tracker assigns — a greedy centroid tracker,
a re-identification model, or nothing at all. StoreLens never joins two entity
IDs across cameras or sessions by similarity, and never stores face embeddings,
biometric templates, or raw re-identification vectors — only the opaque
identifier a worker already chose to send. `identity_scope`
(`worker_run`/`source`/`workspace`, defaulting to the narrowest) is the worker's
own declaration of how far it's safe to treat two observations as "the same
entity"; the platform trusts that declaration rather than inferring identity
itself. This is a deliberate scope limitation, not an oversight: solving
cross-camera re-identification correctly is a different, much larger problem
than "derive dwell from a worker's own tracker," and conflating the two would
have both delayed this redesign and made a much stronger, riskier claim about
what the system verifies.

## How replayability is preserved

Every observation records the zone/calibration/surface/zone-view **revision** in
effect at ingestion. Editing a zone's polygon, recalibrating a camera, or
changing a zone-view's membership rule only affects observations ingested after
the edit — historical rows keep the geometry that actually produced their
`x_map`/`y_map`/`zone_id` at the time. Because every derived fact (visits,
dwell, state intervals, measurement aggregates) is computed at read time from
raw observations plus those recorded revisions — never cached or materialized —
the entire analytical surface is deterministically reproducible from the append-
only `events` table. Current-value read models
(`GET /observations/latest`) are the same: computed live from the same table,
not a second write path that could drift from it.

## Consequences

- Accepted: two generations of ingestion contract coexist in one table
  indefinitely (or until a future migration removes the legacy path). This is
  the deliberate lowest-risk migration the redesign asked for — no data reset,
  no forced rewrite of historical rows.
- Accepted: the dashboard's Setup area still exposes `Sources` as a separate
  top-level tab rather than being folded into a single consolidated "Setup"
  section — a lower-risk choice under this change's scope than restructuring
  working navigation.
- Known gap: there is no dashboard UI yet to create an `analysis_condition`
  alert rule (REST/MCP only) — the alert *model* is unified, but the rule
  builder UI wasn't extended to the general case in this pass.
- Known gap: latest-value read models are recomputed per request rather than
  incrementally maintained; correct at this data scale, but would need
  revisiting if per-request cost becomes material.
