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
