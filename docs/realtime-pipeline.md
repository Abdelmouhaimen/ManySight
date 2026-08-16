# The realtime pipeline

ManySight is built for camera-rate input: four cameras submitting 60 processed
frames a second each is 240 complete `DetectionSample`s a second. Two things have
to be true at that rate, and they pull in opposite directions.

> **Raw history favours completeness. Live state favours freshness.**

Every accepted sample is durably persisted, and none is ever skipped. But the
combined view of the space must show what is happening *now* — never a queue of
camera frames from several seconds ago being worked through in order.

This document describes how the pipeline satisfies both. See
[architecture.md](architecture.md) for the surrounding system and
[multiview.md](multiview.md) for what fusion decides.

## The two paths

```
   camera worker
        │  POST /api/v1/detection-samples
        ▼
   validate → enrich (project, match zones)
        │
        ├──────────────► ONE TRANSACTION ──────────────►  events            (append-only)
        │                raw rows + source-current       source_current_*   (read model)
        │                                                       │
        │                                              HTTP 200 returns here
        ▼
   realtime coordinator  (in memory)
        │  newest sample per source; groups marked dirty
        ▼
   monotonic scheduler, at most 100 Hz
        │  dirty groups only; freshest state only
        ▼
   group tick  →  fused_current_entities, zone_current_occupancy
                  fused_observations, zone_occupancy_observations  (derived history)
```

The response returns once the raw evidence and the source's current state are
committed. Cross-camera fusion happens after that, on its own clock.

## Latest-wins applies only to live computation

If a camera submits frames 100, 101, 102 and 103 between two fusion ticks:

* **History**: all four are in `events`, each with its completion marker and its
  detections. Nothing is dropped, ever.
* **Live**: `source_current_samples` holds frame 103, and only 103 takes part in
  the next fusion. Frames 100–102 are reported as **coalesced live updates** —
  the metric is `realtime.live_updates_coalesced`. They are not "dropped
  observations", and no metric in the system will ever call them that.

The coordinator holds only what the next tick needs: the newest sample per
`(workspace, source, entity type)`, a sequence number per group, and which
sequence each group last consumed. No history lives in memory.

## The scheduler

`STORELENS_LIVE_TICK_INTERVAL_S` (default 0.01 s) is a **maximum cadence**, not a
rate. Four rules define it:

**Dirty-only.** A group whose sources have produced nothing new since its last
tick is skipped. A 30 FPS deployment therefore fuses about 30 times a second, not
100 — `realtime.ticks_skipped_clean` counts the difference.

**Monotonic deadlines.** Ticks are scheduled against `time.monotonic()` deadlines
(0 ms, 10 ms, 20 ms…), never as `run; sleep(period)`. A 4 ms tick does not turn
the period into 14 ms.

**No backlog.** If a tick overruns, the deadlines it missed are counted
(`realtime.deadlines_missed`) and dropped. The loop resumes at the next future
deadline using whatever the newest source state is by then. Live state never
works through a queue of obsolete frames.

**Race-safe.** A group's dirty marker is a sequence number. A tick records the
sequence it saw and only clears the marker if it is unchanged when the tick
finishes, so a sample accepted mid-tick cannot be marked clean by it.

Only sources whose state advanced are re-associated. If camera 2 alone produced a
new frame, only camera 2 is staged — but the refresh that follows still reads
every source's current entities, so the fused position reflects all four cameras
subject to the group's existing freshness rules.

## Reads are never stale

Every read of fused state — `GET /multiview/current`, `GET /multiview/occupancy`,
a `fused_entity` analytics query, and the periodic alert loop — drains any
pending group tick before answering. An API consumer can therefore never observe
combined state older than the newest committed sample; the scheduler is what
keeps that drain almost always a no-op.

A test that reads `fused_*` tables straight from SQLite has bypassed that
guarantee and must call `helpers.sync_live_state()`.

## What a group tick does

For each source whose state advanced, in `(timestamp, source_id, sample_key)`
order — the order separate arrivals would have produced:

1. associate that source's local tracks against active fused entities
2. recompute just the entities that stage touched

and then once, at the end:

3. recompute every active fused entity, end the ones with no fresh evidence,
   recompute zone occupancy, and append derived history

Step 2 is what makes a coalesced tick decide like separate arrivals: the next
source sees the entity the previous source created or moved instead of minting a
duplicate identity. It deliberately does *not* end entities — inside a tick, a
source that has not been staged yet still points its member rows at its previous
sample key, and treating that as missing evidence would end live entities.

**Consequence for derived history.** `fused_observations` and
`zone_occupancy_observations` get one row per entity per *tick* rather than per
camera frame. That is the intended effect of coalescing: they are derived
history, describing what fusion concluded and when. Raw evidence is unaffected.

## Freshness and quality

Quality is unchanged: `known` when every source in the group has a fresh
completed sample, `partial` when some do, `unknown` when none do. A high fusion
rate never makes stale data look current — freshness is evaluated from sample
timestamps against the group's `track_age_s`, not from "a snapshot exists". A
camera that stops has its evidence age out; the other cameras keep updating, the
group degrades to `partial`, and nothing queues waiting for the stopped camera.

## Alerts

| Kind | Evaluated |
| --- | --- |
| `dwell_exceeds`, `occupancy_exceeds`, `state_alert`, `event_match` | synchronously with ingestion, on the batch that just arrived, plus the periodic loop as backstop |
| `analysis_condition`, `query_condition` | the periodic loop only — they evaluate a saved analysis over a trailing window and track `for_seconds` edge state across polls |

Realtime alerts are not delayed by the poll interval. When a workspace has no
enabled rules — the common case at camera rate — ingestion opens no connection
and no transaction for alerts at all.

## Configuration caching

Zones (with prepared containment geometry), calibrations, projection surfaces,
zone views, multiview group definitions, the enabled-rule count and the active
space revision are cached per workspace database.

Invalidation is driven by configuration writes, never by a TTL. Every statement
executed through `db.ex`/`db.exmany` is classified once and memoized; a write to
a configuration table bumps a generation and the next reader rebuilds. Writes
that only touch runtime columns on those tables — `event_count`,
`last_ingestion_at`, `last_observation_at`, `last_fired_at`,
`condition_state_json` — are not configuration and do not invalidate. Two paths
drive a raw connection and invalidate explicitly: `db.init_db()` and the guided
demo's promotion transaction.

`tests/test_config_cache.py` proves each mutation route through the public API:
change the configuration, submit the next sample, require the new semantics.

## Execution model

**One process owns a workspace database.** Live latest-state, the dirty set and
the scheduler are in-process, so two ingesting processes against one workspace
would each hold a partial view of the scene. Run one uvicorn worker per
workspace. `GET /api/v1/realtime/metrics` reports the detected model and warns if
`WEB_CONCURRENCY`/`UVICORN_WORKERS` says otherwise.

Ingestion and fusion both write, and SQLite serializes writers anyway, so the
pipeline is deliberately single-threaded: an in-process write mutex per workspace
orders writers instead of leaving them to SQLite's busy handler, a camera-sized
batch is processed on the event loop, and bulk batches and fusion ticks run on
one dedicated pipeline thread.

## Startup and shutdown

On startup the coordinator rebuilds its bookkeeping from
`source_current_samples` and marks every group for one reconciliation tick, so
fused state becomes coherent again without waiting for a camera to send a new
frame. Seeded snapshots are recorded as already consumed: a restart refreshes,
it does not re-associate evidence that is already persisted.
`current_state.rebuild_from_history()` still rebuilds the source-current read
model from `events`.

On shutdown the scheduler stops accepting work, the in-flight tick is awaited,
and anything still dirty is fused before the process exits.

## Durability

`STORELENS_SQLITE_SYNCHRONOUS` defaults to `NORMAL`, the standard setting for
WAL:

* **NORMAL** — the commit is written to the WAL before the request returns but
  not fsync'd. The database is never corrupted, and accepted evidence survives a
  crash of this process. A host power loss or kernel panic can lose the most
  recent transactions.
* **FULL** — fsync on every commit, so a committed sample survives power loss.
  Measured at roughly 5 ms per commit on ordinary hardware, i.e. a ceiling near
  200 commits/second for the whole workspace. A 4 × 60 FPS deployment submits 240
  samples/second and cannot be served under it.

Set `FULL` where power-loss durability of the last few frames matters more than
keeping up with the cameras.

## Metrics

`GET /api/v1/realtime/metrics` returns cumulative counters, gauges, and p50/p95/
p99 over a bounded ring of recent duration samples. Nothing in the pipeline reads
them back, and `raw_evidence_dropped` is structurally zero — no code path can
increment it. `POST /api/v1/realtime/metrics/reset` zeroes them.

| Metric | Meaning |
| --- | --- |
| `ingestion.observations`, `ingestion.completed_samples` | accepted rows and committed samples |
| `ingestion.endpoint_duration_s`, `ingestion.process_duration_s`, `ingestion.raw_transaction_s` | endpoint total, pipeline, and the durable write |
| `ingestion.inline_batches` / `offloaded_batches` | event loop vs pipeline thread |
| `realtime.source_updates`, `realtime.live_updates_coalesced` | published samples, and how many were superseded before a tick used them |
| `realtime.ticks_requested` / `ticks_executed` / `ticks_skipped_clean` | scheduler cadence and dirty-only behaviour |
| `realtime.deadlines_missed` | missed deadlines counted and dropped, never queued |
| `realtime.tick_duration_s`, `realtime.source_to_combined_state_s` | fusion cost and end-to-end freshness |
| `fusion.source_stages`, `fusion.candidate_pairs`, `fusion.stage_*_s` | work inside a tick |
| `coordinator.oldest_unconsumed_live_update_s` | current live-state lag |

## Load testing

```bash
python scripts/load_test_realtime.py --cameras 4 --fps 60 --duration 30
python scripts/load_test_realtime.py --scenario asymmetric      # 60/30/60/15 FPS
python scripts/load_test_realtime.py --scenario stop-camera     # one source stops mid-run
python scripts/load_test_realtime.py --scenario overload        # contention during ticks
```

The harness starts a real uvicorn server in its own process against a throwaway
workspace and drives it over real HTTP, so the load generator does not share the
server's interpreter. It reports sustained input, scheduler behaviour, latency
percentiles, and verifies durable sample counts against what was accepted. Its
exit status is non-zero if any accepted sample is not durable.
