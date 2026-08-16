---
name: multiview-fusion
description: Use for overlapping cameras, cross-camera anonymous track association, fused live state, and fused occupancy. Covers calibration compatibility, explicit groups, gates, quality, and why raw local track IDs must never be counted across cameras.
---

# Multiview fusion

Load [`manysight-core`](../manysight-core/SKILL.md) first.

Multiview associates **active source-local tracks** into anonymous physical tracks. It does
not identify people.

## Prerequisites

1. Every intended source belongs to one mapped space and shares compatible **metric world
   geometry**. Import rich calibrations where available; do not mix rich and planar-only
   sources in one group.
2. Complete samples arrive from each source inside the group's time tolerance.
3. The group is **explicit**. Overlap is never inferred.

## Configuring

1. `inspect_workspace()` for calibration status and existing groups — reuse before creating.
2. `configure_multiview_group(name, source_ids, ...)`. Choose `time_tolerance_s`,
   `spatial_gate_m`, and `track_age_s` from calibration error, sampling rate, walking speed,
   and camera topology. Do not guess an enormous gate to force associations.
3. Run one local worker per source. Each submits complete samples with its **own** local
   track IDs, one exact timestamp, and one `sample_id`.
4. `inspect_perception(source_ids=[...])` for per-source freshness and multiview readiness.
5. Inspect fused entities and their **member evidence**, not only the fused count.

## What fusion is, and is not

- Association uses geometry, time, trajectory, and topology with global assignment.
- There is no appearance/ReID signal in the current baseline, and none is required.
- Fused IDs are anonymous physical-track estimates. They are not identity.
- Source-local tracker IDs stay local. Never join them across cameras yourself.
- Association can switch at close crossings or under poor calibration/synchronization.

## Counting

Cross-camera occupancy uses **fused entities inside the canonical zone**:

```text
subject   fused_entity
measures  ["current_occupancy"]
filters   {"group_ids": [g], "zone_ids": [z], "entity_types": ["person"]}
```

It is never a count of camera bounding boxes, never `DISTINCT` raw local tracker IDs across
cameras, and never frontend polygon membership. Two cameras seeing one person is one
person.

## Quality

`known` (every group source fresh), `partial` (some fresh), `unknown` (none). These describe
source coverage and must never be read as an observed zero. A stale required camera makes
the answer unknown, not empty.

## Before publishing

Test overlap deduplication, a non-overlap case, zero samples, worker staleness, and a close
crossing before attaching a query or alert to fused state.

For looped replay, end current source and fused identity state at every rewind, prefix
source-local IDs with the playback epoch, and retain only bounded raw and derived epochs.
Never join identities across a media loop boundary.
