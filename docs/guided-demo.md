# Guided four-camera demo

The **Try Demo** workflow is a deterministic, isolated StoreLens walkthrough using
cameras 1–4 from NVIDIA's synthetic `mtmc_12cam` warehouse dataset. It answers one
fixed question:

> Alert when at least two anonymous fused person tracks are in Aisle 04.

The demonstration has three deliberately separate stages:

```text
fixture generation:  NVIDIA video → YOLO11n + ByteTrack → raw DetectionSample fixture
cache generation:    raw fixture → real StoreLens derivation → derived replay cache
playable runtime:    one master clock → video + boxes + cached StoreLens state
```

Playable runtime is not live fusion and is not a worker. It does no inference, ongoing
projection, multiview optimization, query recomputation, or alert evaluation.

## Install optional NVIDIA media

StoreLens does not redistribute NVIDIA videos or model weights:

```powershell
python demo/fetch_nvidia_mv3dt.py
```

The fetcher downloads NVIDIA's archive, prints its SHA-256, rejects unsafe archive
paths, and installs the dataset below ignored `data/demo-assets/`. Use
`STORELENS_DEMO_ASSET_DIR` for another extracted `datasets/mtmc_12cam` path. Review the
applicable NVIDIA terms; StoreLens's repository license does not grant rights to that
media.

## Raw detection fixture

`demo/fixtures/nvidia_mv3dt_yolo11n_bytetrack.jsonl` contains one metadata record and
2,408 camera-frame records: 602 source frames for each of four cameras at 30 FPS. Each
frame is a public schema-v2 `DetectionSample` with:

- source key, media-relative time, source frame index, and opaque sample ID;
- zero or more source-local tracker detections;
- YOLO confidence, corner-form bounding box, and bottom-center point;
- detector/tracker, model-file hash, library, and CUDA provenance in metadata;
- no map point, zone assignment, fused ID, KPI, or alert.

An empty `detections` list is a complete known-zero sample. Fixture authors do not
create a generic `detection_frame_count` measurement; StoreLens retains that record only
as an internal/legacy normalization detail.

Validate the fixture without model dependencies:

```powershell
python demo/validate_mv3dt_fixture.py demo/fixtures/nvidia_mv3dt_yolo11n_bytetrack.jsonl
```

Regenerating detections is an offline maintainer operation requiring the downloaded
dataset, YOLO weights, Ultralytics, OpenCV, and PyTorch:

```powershell
python demo/generate_mv3dt_fixture.py `
  --dataset C:\path\to\datasets\mtmc_12cam `
  --model C:\path\to\yolo11n.pt
```

## StoreLens-derived replay cache

`demo/build_mv3dt_demo_fixture.py` configures an isolated real StoreLens workspace,
imports the four validated NVIDIA projection matrices, constructs Aisle 04, and sends
synchronized raw samples through normal observation enrichment, complete-sample
materialization, multiview association, saved-query execution, and query-alert
evaluation at 10 Hz. It writes
`demo/fixtures/nvidia_mv3dt_derived_replay.json`.

```powershell
python demo/build_mv3dt_demo_fixture.py `
  --asset-root C:\path\to\datasets\mtmc_12cam
```

The artifact records raw-fixture, recipe, media, geometry, fusion configuration,
derivation-code, and canonical payload hashes. The builder uses a deterministic
simulated evidence clock and deterministic anonymous fused IDs, so identical inputs
produce identical geometry and timeline payloads. Runtime refuses a cache whose recipe,
raw fixture, or payload hash does not match.

Each of the 201 derived samples contains:

- media time and source frame index;
- four complete source-sample references;
- StoreLens fused entities with member provenance;
- the real saved-query value, quality, `as_of`, and evidence window;
- real edge-triggered alert events produced at that sample.

Tests also run a small fixture slice through the real pipeline rather than trusting only
the committed cache.

## Aisle 04 geometry

Only Cameras 3 and 4 show Aisle 04. Cameras 1 and 2 intentionally have no zone view.
The image polygons use the 1920×1080 source coordinate system:

```text
Camera 3: (945,1080), (1720,1080), (1235,0), (960,0)
Camera 4: (0,980), (735,1080), (881,0), (617,0)
```

The validated floor calibration projects Camera 3 to approximately:

```text
(17.260,22.967), (20.552,22.967), (21.057,4.708), (17.323,4.708) metres
```

Camera 4 projects to approximately:

```text
(20.873,6.899), (17.071,6.311), (17.213,24.336), (20.851,24.310) metres
```

Camera 3 creates the first canonical polygon. Camera 4 is an explicit
`extend_zone_from_view` operation. StoreLens unions the overlapping physical
contributions into one metric Polygon at zone revision 2 and records original pixels,
projected points, calibration revision, view revision, operation, and resulting zone
revision. Coordinates are never moved merely to improve appearance.

## One authoritative playback clock

The server persists a lightweight absolute clock anchor. The browser maintains one
app-level `requestAnimationFrame` clock derived from that anchor. At master time `T`:

- all four MP4 elements seek/play against `T`;
- the camera overlay uses source frame `floor(T × 30)`;
- the analytical state is the latest cache sample whose time is `<= T`;
- fused positions interpolate only when the same fused ID exists in both adjacent
  derived samples;
- occupancy, quality, evidence, and alerts remain stepwise and are never interpolated;
- dashboard widgets use the cached StoreLens saved-query result, not frontend box or
  polygon counting.

The optional **Debug sync** display reports master time, video frame, box frame, derived
sample time/index, replay epoch, and each video's presented media time. There is no
“Restart evidence” control and no runtime processing queue for video to wait on.

At the media boundary all four videos rewind together. The absolute clock continues,
the epoch increments, the relative derived timeline starts at zero, and rendered fused
IDs are namespaced as `e{epoch}:{fused_id}`. The renderer does not interpolate across a
loop boundary.

## Guided walkthrough layer

Starting the guided demo adds a walkthrough on top of the real interface. There is no
separate demo application: the ManySight UI stays visible and interactive underneath a
small progress card, a slight dimming layer, and a spotlight around the control or area
the current step is about.

The card lives at the top left of the content area, clear of the sidebar, and survives
navigation and a browser refresh for as long as its demo session is active. It stays
parked: a spotlight covering most of the viewport is treated as a region rather than a
control, a slight clip is tolerated, and the card only leaves its home corner when it
would genuinely cover the element being explained — returning as soon as that step ends.
Steps are paced to be watched rather than skimmed, and each holds its finished state on
screen before moving on. It reports
what is happening now, what is already complete, what happens next, and whether
StoreLens is doing something automatically or waiting for a click. States are `pending`,
`active`, `loading`, `complete`, `error`, and `waiting_for_user`, each shown with an icon
and text rather than colour alone. Escape releases the dimming, the card can be collapsed
or hidden, and **Exit demo** always remains reachable.

A guided session is deliberately incomplete when it starts. `POST /api/v1/demo/sessions`
with `mode=guided` creates only the camera and space setup — the mapped space, the four
sources with their placements and imported calibrations, and the calibrated multiview
group. The monitored zone, its two camera views, the saved query, the alert rule, and the
dashboard do not exist yet, so nothing on screen claims to exist before the step that
explains it: the floor map has no Aisle 04, and no camera carries a zone trace.

The walkthrough then applies those stages one at a time through
`POST /api/v1/demo/sessions/{id}/apply-request` with a `stage` of `zone_seed`,
`zone_extend`, `query`, `alert`, or `dashboard`. Each stage runs the same real StoreLens
operations the prepared workspace uses, in the same order, and appends the same
action-log entries; stages are ordered (a stage refuses to run before its prerequisite)
and idempotent, so a refresh or a retry never duplicates geometry. `mode=learn`, the
explore-only demo, is still configured up front, and the offline cache builder still
constructs the whole workspace in one pass, so the derived replay cache is unaffected.

Two teaching paths are offered once the four demo sources are ready:

- **Set it up automatically** presents the prepared demo plan, placements, and imported
  calibrations.
- **Show me how** navigates to the real Setup area and spotlights the actual
  **Digitize plan** control, then the actual **Calibrate camera** control for Camera 1.
  The user works in the real digitizer and the real calibration dialog; the tour never
  substitutes its own controls. Both the digitizer canvas and the calibration dialog's
  floor map keep the bird's-eye plan behind them, so matching points has a recognisable
  floor to aim at.

The practice detour writes real data, so it is followed by a restore step. Saving a
traced plan legitimately clears placements and floor calibrations, and
`POST /api/v1/demo/sessions/{id}/restore-practice-space` measures the trace and then
reinstates the prepared recipe map, camera placements, and imported NVIDIA matrices —
the same "practice, compare, restore" contract as
`restore-practice-calibration`. Both paths therefore converge on one validated state
before the walkthrough continues.

The request narration is explanatory only. The card shows the sentence a user might send
to a coding agent — *"Alert me when there are at least 2 people in Aisle 04."* — and says
plainly that nothing is running an agent and that the demo reproduces the configuration
such a request needs. There is no simulated agent chat, terminal, tool call, or reasoning.

Spotlight targets are resolved from stable `data-demo-tour` hooks, never from
structural CSS selectors, so moving markup around cannot silently break the
walkthrough. The current registry is:

```text
try-demo, nav-<route>                      shell navigation
demo-start-guided, demo-exit               demo entry and exit
demo-camera-grid, camera-<n>-tile          replay video grid and one camera
demo-occupancy, demo-alert                 cached KPI and alert panels
setup-tab-<name>, sources-list             setup areas
source-row-<n>, camera-calibrate-<n>       one source and its calibration control
digitize-plan, floor-map                   plan digitizer and the metric map
live-floor-map, dashboard-kpi              Live scene and the generated KPI widget
```

A step that cannot find its target waits through the route change and render, then
reports a readable fallback with **Look again** and **Skip this step**; it never
advances a required interaction on its own.

Everything the card reports is read from real state: session results and action log,
demo-workspace sources, zones, zone views, saved query, alert rule and dashboard, and the
committed replay cache. Progress presentation is sequenced, never invented — the four
source rows appear one at a time because their real IDs already exist, an uncalibrated
camera is never shown as calibrated, a camera's zone trace appears only once that zone
view has been created, and the alert step reports the occupancy StoreLens derived for the
sample that actually fired rather than a fixed number. The walkthrough
performs no projection, fusion, query, or alert evaluation of its own and takes no part in
playback timing.

## Isolation, learning, and promotion

Every session uses a temporary SQLite workspace selected by an opaque browser session
header. Normal requests continue to use `data/storelens.db`. Running sessions recover
their persisted clock after a server restart; paused sessions remain paused; sessions
expire after 24 hours.

The guided action log contains identifiers returned by real setup operations: workspace
inspection, source/capture creation, calibration imports, Camera 3 and Camera 4 traces
and projections, canonical union, multiview group, saved query, dashboard, and alert.
The learn path links to the normal plan, calibration, Live, Evidence, Dashboard,
Sources, and Review interfaces.

**Discard demo** removes the isolated workspace. **Keep camera & space setup** copies
only the map, four sources, placements, calibrations, and multiview group. Aisle 04 and
its views, query, dashboard, alert rule, and review events remain demo-only. Raw samples
are opt-in; when selected, samples up to the current master time are materialized through
the real ingestion path, remapped to promoted source IDs, detached from demo-only zone
links, and tagged with promotion provenance.

After promotion, the allowlisted local MJPEG supervisor loops the four source videos on
one clock. That simulated camera service is separate from browser-native guided playback
and is not a general process runner or production camera gateway.

## Scope

The demo proves deterministic platform wiring, not operational accuracy or production
readiness. Validate source authorization and reachability, timestamps, calibration,
model/tracker behavior, fusion gates, geometry, freshness, privacy, retention, alert
thresholds, authentication, and TLS in the intended deployment.
