---
name: perception-workers
description: Use for any worker that submits perception — tracked detections (presence, heatmaps, visits, dwell, flow), numeric measurements (queue length, population counts), or categorical states (fridge open/closed). Covers reuse-before-start, the current submission contract, sampling rate, local environment, and verification.
---

# Perception workers

Load [`storelens-core`](../storelens-core/SKILL.md) first.

A worker runs **on your machine**: it opens the source, runs the model, tracks anonymous
entities, and posts raw evidence. StoreLens derives everything else.

## Before you start anything

1. **`inspect_perception(entity_type, source_ids)`.** If it returns `action="reuse"`, the
   data already exists — stop and query it. Starting a second worker for sources that
   already have healthy perception is a defect, not thoroughness.
2. If coverage is partial, extend the missing sources rather than replacing what works.
3. Check the sources you need are configured and calibrated (`inspect_source`).
4. **`get_worker_recipe(entity_type, tracking, source_ids)`** for the current contract.
5. Inspect your **own** local environment before building anything: an existing project
   virtualenv or conda environment, CUDA and PyTorch availability, model weights already on
   disk. Reuse a compatible environment; do not create a new one by reflex.

> **Authority.** The recipe, `GET /api/v1/observations/contract`, and `/openapi.json` are
> the contract. An example script, a demo worker, or an older worker file found in a
> repository may predate the current API — never treat one as the protocol. If a file on
> disk disagrees with the recipe, the recipe is right.

## Detections — one atomic sample per processed frame

For each processed frame:

1. One exact timestamp and one opaque source-local `sample_id`.
2. Detect and track. `entity_id` is an anonymous tracker ID with an honest
   `identity_scope`, not a verified identity.
3. Build one `DetectionSample` with every detection's pixel evidence. For floor traffic,
   feet or bbox bottom-centre are normally correct; preserve bbox, keypoints, and mask when
   available.
4. Submit the complete envelope once. Use `detections=[]` for a processed empty frame.
5. Never invent a zero-confidence detection for an empty frame.

```python
sample = client.begin_detection_sample(source_id, "person", ts=time.time())
for track in tracks:
    sample.add_detection(
        entity_id=str(track.id),
        bbox_px=(track.x0, track.y0, track.x1, track.y1),
        confidence=track.confidence,
    )
sample.submit()          # detections=[] is a real observed zero
```

The builder posts the preferred atomic envelope (`POST /api/v1/detection-samples`). Legacy
split detection rows plus a matching `detection_frame_count` measurement remain readable
for compatibility, but new workers must not author that internal completion concept, and
must not prefer it over the sample envelope. Do not call `time.time()` separately for
detections belonging to one sample.

## Sampling rate

Local decode, detection, and tracking may run at **full camera FPS** — 30-40 FPS on a
modern GPU is normal and desirable for tracker stability. The **central submission rate is
a separate choice** and is usually lower; submitting every decoded frame is rarely
necessary.

- Current occupancy and zone presence: a few Hz is normally sufficient.
- Dwell and visit boundaries: raise the rate until visit edges are stable.
- Fast movement or tight spatial gates: match the multiview time tolerance.
- There is no globally correct rate. Choose it from the task, state what you chose and
  why, and report `local_fps` and `submission_hz` in heartbeat metrics so
  `inspect_perception` can show them.

## Measurements — a number over time

Use when the answer is a number that changes over time and is not naturally a tracked
entity or a categorical value: queue length, population counts, occupied desks.

- `name` = the metric identity (`"queue_length"`), required — this is what queries filter by.
- `value` = a directly observed number, required. Never a client-side average or a
  time-aggregated total.
- `value_kind` = `gauge` (instantaneous, the default and usually right), `delta` (an
  increment observed this sample), or `cumulative` (a monotonically increasing producer
  counter — StoreLens detects resets, so a worker restart never yields a negative rate).
  Aggregation depends on getting this right.
- `label` optionally qualifies *which instance*, not *what is measured*.
- A measurement is zone-assigned only if it carries geometry (e.g. a `point_map` hint) or
  shares an `entity_id` with a recent detection. This is by design, not silent mis-zoning.

Submit one sample per interval. Never sum `gauge` samples to make a total.

## States — a categorical value over time

Use for equipment or scene states: doors, lights, shutters, machine on/off.

- `name` = the state key (`"door_state"`), `label` = the observed value now
  (`"open"`/`"closed"`), `source_id` set.
- `entity_id` when several independently stateful things share a source and name.
- Send **every sample, including runs of identical values.** StoreLens coalesces identical
  consecutive samples into intervals and derives every duration and transition.

Do not detect the flip yourself and send only on change — `state_change` is the retired
contract and is rejected as `legacy_derived_observation`. Do not post a computed duration;
it is ignored. A source that stops reporting goes stale rather than extending its interval
forever.

## Lifecycle

Register a job before posting, register the worker instance you actually started, and
heartbeat every 5-15 s. The heartbeat response carries `should_stop` and
`restart_requested` — obey them and exit cleanly. StoreLens never launches, kills, or
relaunches your process; `request_worker_state("restart")` does nothing without a
supervisor. Never register a worker you did not start.

## Verify — do not assume

1. `inspect_perception(...)` — heartbeat, complete samples, freshness, submission rate.
2. `GET /api/v1/observations/latest-frames` — the current complete sample per source, with
   the projection and zone StoreLens assigned.
3. `GET /api/v1/multiview/current` — fused entities with member evidence, if fusing.
4. `run_query(...)` — the actual question, with its quality.

Claiming a worker is healthy without checking its heartbeat, or claiming observations are
flowing without reading them back, is the failure mode this section exists to prevent.

## Pitfalls

- Unstable tracker IDs destroy dwell and flow semantics.
- A torso projected through a floor homography is usually wrong; use feet or a named plane.
- Missing samples mean stale/unknown evidence, never observed zero.
- Geometry-only multiview association is not biometric ReID and can switch at close
  crossings or under poor calibration.
