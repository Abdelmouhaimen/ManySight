# Guided four-camera demo

The bundled **Try Demo** workflow is a playable, isolated StoreLens walkthrough. It
uses NVIDIA's four-camera synthetic warehouse sample and answers one fixed question:

> Alert when at least two anonymous fused person tracks are in Aisle 04.

The demo is evidence-backed. A numerical fixture was precomputed once with YOLO11n
and ByteTrack on CUDA. At runtime StoreLens progressively submits those source-local
results through the normal schema-v2 ingestion path, performs floor projection, assigns
the canonical metric zone, associates anonymous active tracks centrally, executes a
saved query, renders its dashboard widget, and evaluates a query-backed alert. Runtime
replay does not import Torch, Ultralytics, model weights, or CUDA.

## Install the optional media

StoreLens does not redistribute NVIDIA videos or model weights. Download the archive
from NVIDIA on demand:

```powershell
python demo/fetch_nvidia_mv3dt.py
```

The script downloads the NVIDIA-hosted `datasets.zip`, prints the downloaded archive's
SHA-256 for an audit trail, checks archive paths before extracting, and installs the files
below ignored `data/demo-assets/`. NVIDIA does not publish a pinned digest at this URL,
so the printed value is not an authenticity guarantee. A different local
location can be selected with `--destination`; set `STORELENS_DEMO_ASSET_DIR` to the
extracted `datasets/mtmc_4cam` directory.

The sample is described in NVIDIA's
[Multi-View 3D Tracking documentation](https://docs.nvidia.com/metropolis/deepstream/dev-guide/text/DS_MV3DT.html)
and hosted by the [NVIDIA DeepStream repository](https://github.com/NVIDIA/DeepStream).
Review the applicable NVIDIA terms before downloading or using the assets. StoreLens's
repository license, when one is added, does not grant rights to NVIDIA media.

## Fixture provenance and regeneration

`demo/fixtures/nvidia_mv3dt_yolo11n_bytetrack.jsonl` contains:

- one versioned metadata record with video timing, producer configuration, Ultralytics
  version, and the exact model SHA-256;
- one record for every processed source/timestamp, including an explicit detection list
  and matching `detection_frame_count`;
- source-local track IDs, confidence, corner-form bounding boxes, and representative
  bottom points;
- no source images, canonical zones, map points, fused identities, or analytics.

Validate it without model dependencies:

```powershell
python demo/validate_mv3dt_fixture.py demo/fixtures/nvidia_mv3dt_yolo11n_bytetrack.jsonl
```

Regeneration is an offline maintainer operation and requires a local YOLO11n weight,
Ultralytics, OpenCV, CUDA-enabled PyTorch, and the downloaded dataset:

```powershell
python demo/generate_mv3dt_fixture.py `
  --dataset C:\path\to\datasets\mtmc_4cam `
  --model C:\path\to\yolo11n.pt
```

Fixture changes must be reviewed as evidence changes, not reformatted casually. The
validator ensures synchronized camera timestamps, monotonic per-camera order, stable
fields, and absence of StoreLens-owned derived values.

## Isolation and playback

Each demo session gets a temporary SQLite workspace selected by an explicit browser
session header. Normal API requests continue to use `data/storelens.db`; demo requests
cannot see or mutate normal rows. The public session response never contains the local
temporary path.

The browser persists only the opaque session ID. After a local server restart, a running
session reconnects to its persisted clock and workspace instead of creating another one;
paused sessions remain paused. Sessions expire after 24 hours and are cleaned up.

Four browser MP4 elements read the original local files directly and follow one
server-owned position. Observations are not preloaded. The replay controller posts only
frames whose media time has elapsed. Every source frame uses one exact timestamp and
sample ID, zero or more detections, and exactly one `detection_frame_count` completion
marker. Worker/job rows are not fabricated because replay is not a live worker.

The controller advances at real time between fixture samples but freezes its logical
clock while StoreLens derives a sample. This prevents slower machines from exposing
future video before the corresponding evidence or starving API reads with a catch-up
burst. All four videos may therefore play slightly slower than wall time under load, but
they remain aligned with one another and with StoreLens state.

The demo multiview group uses a 15-second freshness horizon because its committed
fixture is sampled at 1 Hz and synchronous derivation can be slower on modest CPUs. This
is recipe configuration, not a production default; operational groups should use a
horizon appropriate to their actual camera rate and latency budget.

The first real threshold event is held on screen for two seconds so a polling browser can
render the successful query and quality. Playback then continues and loops normally; the
controller does not fabricate or manually fire the alert.

At each loop boundary StoreLens starts a new monotonic epoch, resets source/fused current
identity state, and gives source-local IDs an epoch prefix. Raw and derived replay history
is pruned to a bounded number of epochs. Missing evidence remains unknown; replay does
not invent empty frames.

## Guided and learn paths

The automatic path creates the mapped space, imports all four 3x4 camera matrices,
projects predetermined camera pixels into the real Aisle 04 metric zone, creates the
camera-specific views and multiview group, then creates one saved occupancy query,
dashboard widget, and alert rule. The timeline displays returned IDs and verification
results from these real operations.

The learn path links to the same Setup, plan-digitizer, calibration, Live 3D, Evidence,
Dashboard, Sources, and Review pages used by a normal workspace. It guides the user to
inspect the supplied map and practice one real homography with Camera 1's recorded video.
StoreLens computes the practice calibration, compares it with the validated matrix at
known reference pixels, displays the metric difference, and then restores the validated
matrix. Cameras 2–4 remain on their validated matrices. Exploratory input therefore
teaches the actual interaction without silently breaking fixture fusion. Changes remain
isolated.

## Exit and promotion

Exit offers two explicit choices:

- **Discard demo** permanently removes the temporary database.
- **Keep camera & space setup** transactionally copies the map, four sources, camera
  placements, calibrations, and multiview group into the normal workspace.

Promotion deliberately excludes Aisle 04, its camera views, the saved query, dashboard,
alert rule, and fired alerts. Raw replay observations are excluded by default and require
an explicit checkbox. When selected they are remapped to promoted source IDs, detached
from non-promoted zone/view IDs, and tagged with demo provenance.

Only after a successful promotion does StoreLens start the tightly scoped local
four-camera MJPEG supervisor. It serves exactly the allowlisted sample videos on one
shared clock and is not a general process runner or production camera gateway. StoreLens
restarts that known supervisor on a later platform launch while the promoted sources
still reference the promoted demo session. It never starts CV inference automatically.

## Production validation

The demo proves platform wiring, not model or deployment quality. Before operational use,
validate camera authorization, source reachability, timestamp synchronization,
calibration error, detector/tracker behavior, fusion gates, zone geometry, freshness,
alert thresholds, authentication, TLS, retention, and privacy requirements using the
actual deployment environment.
