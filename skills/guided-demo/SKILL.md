---
name: guided-demo
description: Use when a user asks about the Try Demo walkthrough, or when demo state might be confused with real workspace state. Explains what the guided demo is, what it is not, its isolation, and its promotion boundary.
---

# Guided demo

Load [`manysight-core`](../manysight-core/SKILL.md) first.

The **Try Demo** workflow is a deterministic, isolated ManySight walkthrough over four
cameras from NVIDIA's synthetic `mtmc_12cam` warehouse sample. It answers one fixed
question: *alert when at least two anonymous fused person tracks are in Aisle 04.*

## Three separate stages

```text
fixture generation   NVIDIA video → YOLO11n + ByteTrack → raw DetectionSample fixture
cache generation     raw fixture → the REAL ManySight pipeline → derived replay cache
playable runtime     one master clock → video + boxes + cached ManySight state
```

Only the middle stage runs the platform's derivation. Stages one and two are offline
maintainer operations.

## What runtime is not

Playable runtime performs **no** inference, ongoing projection, multiview optimization,
query recomputation, or alert evaluation. It creates no worker row and no heartbeat, and its
producer kind is `replay`.

**Never describe replay as live inference or live fusion.** The numbers on screen were
derived offline by the real pipeline and are served from a provenance-hashed cache; the
runtime advances one clock over them.

The demo is the one narrow exception to the bundled-media rule: it serves an allowlisted set
of local NVIDIA assets plus a versioned fixture. It is not a general media proxy.

## Isolation

Every session uses a temporary SQLite workspace selected by an opaque browser session
header. Normal requests continue to use the real workspace database. Sessions expire after
24 hours; running sessions recover their persisted clock after a server restart.

A guided session is deliberately incomplete when it starts: only the mapped space, the four
sources with placements and imported calibrations, and the calibrated multiview group exist.
The monitored zone, its two camera views, the saved query, the alert rule, and the dashboard
are applied one stage at a time as the walkthrough explains them, through real ManySight
operations that are ordered and idempotent.

## Promotion

**Discard demo** removes the isolated workspace. **Keep camera & space setup** copies only
the map, four sources, placements, calibrations, and multiview group. Aisle 04 and its
views, the saved query, dashboard, alert rule, and review events stay demo-only.

Do not copy the demo's alert rule into a real workspace, and do not reuse its `>= 2`
threshold for a user request phrased differently — see
[`queries-dashboards-alerts`](../queries-dashboards-alerts/SKILL.md).

Raw samples are opt-in; when selected they are materialized through the real ingestion path,
remapped to promoted source IDs, detached from demo-only zone links, and tagged with
promotion provenance.

## Working on demo state

Demo geometry is validated and committed. Do not "improve" the demo's Aisle 04 polygons,
its playback timing, or its derived cache as a side effect of another task — the cache is
verified against recipe, raw-fixture, geometry, derivation-code, and payload hashes, and
runtime refuses a cache that does not match.

The demo proves deterministic platform wiring, not operational accuracy or production
readiness.
