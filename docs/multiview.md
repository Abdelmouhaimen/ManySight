# Multiview current state

Multiview is an optional central derivation over complete source-local detection samples.
It is enabled only for explicit groups of cameras calibrated into one metric world frame.
Ordinary single-camera ingestion and source-local Live debugging continue to work.

## Preconditions

- At least two sources in the same StoreLens space.
- A usable floor/world calibration for every source.
- Rich calibrations, when present, on every source with identical units and world-frame
  metadata. Rich and planar-only calibrations cannot be mixed in one group.
- A sensible time tolerance, spatial gate, active-track age, and optional neighbor graph.

Workers do not coordinate identities. Each source posts its own `sample_id`, timestamp,
local `entity_id`, confidence, and pixel evidence. StoreLens waits for complete source
samples, projects them independently, then associates active tracks centrally.

## Association and provenance

The baseline is intentionally geometry-first: time gating, metric spatial gating, short
trajectory prediction, topology filtering, and global minimum-cost assignment. It does
not use appearance/ReID. Fused history stores the fused ID, contributing local tracks and
source events, sample keys, map point, zone, confidence, quality, algorithm/version, and
configuration revision. Raw observations remain immutable.

The same source-local track keeps its active fused ID. A distant track is not forced into
an existing identity. A fused identity expires after the configured age when no fresh
member evidence remains.

## Current state and quality

`GET /api/v1/multiview/current` returns anonymous fused tracks and per-group freshness.
`GET /api/v1/multiview/occupancy` returns a zone count with `known`, `partial`, or
`unknown` quality. A complete zero sample is evidence of zero for that source; a stale or
missing sample is not. Historical occupancy snapshots are recorded when source samples
advance and are available through a time-grouped `fused_entity` query.

Live defaults to fused mode and offers source-local debug mode. Member evidence is shown
for inspection, but the UI must not describe fused IDs as recognized or identified people.

## Limits

Geometry-only association depends on calibration and synchronization. Close crossings,
occlusion, large timing skew, and weak topology can cause ID switches. This implementation
does not claim durable identity across long gaps or outside a group's active lifetime.

Looped replay establishes a new epoch at every rewind. Source and fused current identity
state is ended before the next epoch and local IDs are epoch-prefixed, so a track cannot
claim continuity from the end of a clip to its beginning. Historical fused and occupancy
rows are retained only for the configured bounded epoch count.
