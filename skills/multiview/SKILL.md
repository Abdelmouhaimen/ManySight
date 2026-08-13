---
name: multiview
description: Use for overlapping cameras, cross-camera anonymous track association, fused Live state, and fused occupancy. Covers calibration compatibility, explicit groups, quality, and provenance.
---

# Multiview association

Multiview associates active source-local tracks; it does not identify people.

1. Load `storelens-platform`, then inspect sources and calibrations.
2. Confirm every intended source belongs to one mapped space and shares compatible metric
   world geometry. Import rich calibrations when available; do not mix rich and planar-only
   sources in one group.
3. Inspect existing groups before `create_multiview_group`. Choose gates from calibration
   error, sampling rate, walking speed, and camera topology; do not guess an enormous gate.
4. Run one local worker per source. Each submits complete samples with its own local track
   IDs, one exact timestamp, one `sample_id`, and one completion marker.
5. Inspect `get_multiview_status` and `list_current_fused_entities`. Confirm member evidence,
   not only the fused count.
6. Test overlap deduplication, a non-overlap case, zero samples, worker staleness, and a
   close crossing before publishing a query or alert.

StoreLens uses time/distance/trajectory/topology gating and global assignment. No
appearance/ReID signal exists in the current baseline. `known`, `partial`, and `unknown`
quality describe source coverage; missing data must never be interpreted as observed zero.

For looped replay, end current source and fused identity state at every rewind, prefix
source-local IDs with the playback epoch, and retain only bounded raw and derived epochs.
Never join identities across a media loop boundary.
