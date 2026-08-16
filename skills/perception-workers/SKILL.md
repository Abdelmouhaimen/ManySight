---
name: perception-workers
description: Use for any worker that submits perception — tracked detections (presence, heatmaps, visits, dwell, flow), numeric measurements (queue length, population counts), or categorical states (fridge open/closed). Covers reuse-before-start, the current submission contract, tracking frame rate, GPU/CUDA readiness, local environment, and verification.
---

# Perception workers

Load [`manysight-core`](../manysight-core/SKILL.md) first.

A worker runs **on your machine**: it opens the source, runs the model, tracks anonymous
entities, and posts raw evidence. ManySight derives everything else.

## Before you start anything

1. **`inspect_perception(entity_type, source_ids)`.** If it returns `action="reuse"`, the
   data already exists — stop and query it. Starting a second worker for sources that
   already have healthy perception is a defect, not thoroughness.
2. If coverage is partial, extend the missing sources rather than replacing what works.
3. Check the sources you need are configured and calibrated (`inspect_source`).
4. **Inspect your own machine** — see [Hardware and environment](#hardware-and-environment).
   Do this before writing the worker, not after it underperforms.
5. **`get_worker_recipe(entity_type, tracking, source_ids, source_fps)`** for the current
   contract and the rate plan for the source you actually have.

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

## Three rates, not one

| rate | what it is | who decides |
|---|---|---|
| **source FPS** | what the camera, stream, or file delivers | the source |
| **processing FPS** | frames the detector *and tracker* actually consume | you, from hardware |
| **submission Hz** | complete `DetectionSample` envelopes posted to ManySight | you, from the task |

Conflating them produces a worker that looks fine and tracks badly. A tracker is a
temporal algorithm: association degrades with the gap between consecutive frames, and no
amount of central derivation recovers an identity swap. Dwell, visits, flow and multiview
association all rest on that continuity.

**Tracking workloads — person detection + tracking, YOLO + ByteTrack or equivalent,
multiview person tracking — process at least 15 FPS per camera** when the source supplies
that and the machine sustains it. Prefer **30 FPS or source-native** where they are
available. At 5 FPS someone crossing an aisle moves about a metre between frames, wider
than the default fusion spatial gate.

- **Never** silently configure a tracking worker at 1-5 FPS on a source and machine capable
  of substantially more.
- **Source below 15 FPS:** use the source-native rate and report the limitation. Do not
  claim the floor was met.
- **Machine cannot sustain 15 FPS:** do not fake compliance. Report the measured rate and
  recommend the acceleration path (see below).
- **Never hard-code a sleep that caps a capable GPU worker** below the target.

The **submission rate is separate and usually lower** — submitting every decoded frame is
rarely necessary. Gate submission; do not slow the tracker to achieve it. `SubmissionGate`
in the SDK exists for exactly this.

- Current occupancy and zone presence: a few Hz is normally sufficient.
- Dwell and visit boundaries: raise the rate until visit edges are stable.
- Fast movement or tight spatial gates: match the multiview time tolerance.
- There is no globally correct submission rate. Choose it from the task and say why.

`get_worker_recipe(..., source_fps=...)` returns all of this computed as
`sampling.recommendation`: `target_processing_fps`, `target_submission_hz`,
`minimum_processing_fps`, and the rationale.

Report `source_fps`, `processing_fps`, `submission_hz`, `device` and `precision` in
heartbeat metrics so `inspect_perception` can score achieved against target.

## Hardware and environment

ManySight runs no models and cannot see your machine, so **you** determine this — never ask
the user "do you have CUDA?", "which conda environment?", or "please start the worker" when
a shell can answer it or do it.

```python
import sys; sys.path.insert(0, "sdk/python")
from manysight import probe_perception_runtime
print(probe_perception_runtime())
```

One call reports the interpreter and environment kind, `nvidia-smi` presence, driver and
device names, torch version and CUDA build, `torch.cuda.is_available()`, the device name
and compute capability, a recommended device, and whether FP16 is worth enabling. It never
raises — "no GPU here" is an answer, not a failure. Run it **with the interpreter that will
run the worker**: a base environment answering "yes" proves nothing about the venv you are
about to start.

- **Reuse first.** An existing project virtualenv or conda environment that already has the
  accelerated dependencies and weights beats a new one. Do not assume the base interpreter
  is the right one, and do not build a fresh environment by reflex.
- **GPU first when it is there.** Select the CUDA device for supported models. Enable FP16
  only on a CUDA device with compute capability ≥ 7.0, and only after validating output —
  never on a CPU path or an unvalidated runtime.
- **Reuse the worker's existing model and runtime.** Do not introduce another ML framework
  to enable acceleration.
- **CPU is a supported fallback,** not a failure. Target the best rate you can actually
  sustain, measure it, and warn plainly if it is below 15 FPS.

Camera availability, perception runnability, and performance capability are **three
separate questions**. A missing GPU lowers the achievable rate. It never makes a camera
unusable, and never justifies telling a user their camera cannot be used.

## Measurements — a number over time

Use when the answer is a number that changes over time and is not naturally a tracked
entity or a categorical value: queue length, population counts, occupied desks.

- `name` = the metric identity (`"queue_length"`), required — this is what queries filter by.
- `value` = a directly observed number, required. Never a client-side average or a
  time-aggregated total.
- `value_kind` = `gauge` (instantaneous, the default and usually right), `delta` (an
  increment observed this sample), or `cumulative` (a monotonically increasing producer
  counter — ManySight detects resets, so a worker restart never yields a negative rate).
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
- Send **every sample, including runs of identical values.** ManySight coalesces identical
  consecutive samples into intervals and derives every duration and transition.

Do not detect the flip yourself and send only on change — `state_change` is the retired
contract and is rejected as `legacy_derived_observation`. Do not post a computed duration;
it is ignored. A source that stops reporting goes stale rather than extending its interval
forever.

## Lifecycle

Register a job before posting, register the worker instance you actually started, and
heartbeat every 5-15 s. The heartbeat response carries `should_stop` and
`restart_requested` — obey them and exit cleanly. ManySight never launches, kills, or
relaunches your process; `request_worker_state("restart")` does nothing without a
supervisor. Never register a worker you did not start.

## Verify — do not assume

Starting the process is not the finish line, and occasional samples are not health.

1. The process is still alive locally, and no frame or submission backlog is growing.
2. `inspect_perception(...)` — heartbeat, complete samples, freshness, submission rate, and
   `performance`: achieved processing FPS against target.
3. `GET /api/v1/observations/latest-frames` — the current complete sample per source, with
   the projection and zone ManySight assigned. Confirm detections carry `entity_id` when
   tracking is enabled, and that empty complete frames are accepted as known zero.
4. `GET /api/v1/multiview/current` — fused entities with member evidence, if fusing.
5. `run_query(...)` — the actual question, with its quality.

If `performance.state` is `below_target` — tracking below the target rate while the source
supplies more — surface it as a readiness warning with the concrete cause. The usual ones,
in order: CPU inference while a GPU sits idle, CUDA unavailable in the interpreter that
actually started, the wrong environment, too heavy a model, inference configuration, or a
capture/decode bottleneck rather than inference at all.

Claiming a worker is healthy without checking its heartbeat, claiming observations are
flowing without reading them back, or calling a 4 FPS tracker fine because samples arrive,
are the failure modes this section exists to prevent.

## Pitfalls

- Unstable tracker IDs destroy dwell and flow semantics — and the commonest cause is
  processing too few frames per second, not the tracker's configuration.
- A torso projected through a floor homography is usually wrong; use feet or a named plane.
- Missing samples mean stale/unknown evidence, never observed zero.
- Geometry-only multiview association is not biometric ReID and can switch at close
  crossings or under poor calibration.
