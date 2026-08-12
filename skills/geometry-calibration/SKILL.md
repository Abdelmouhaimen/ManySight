---
name: geometry-calibration
description: Use for canonical zones, camera zone views, projection surfaces, planar control-point calibration, rich world-calibration imports, and projection troubleshooting.
---

# Geometry and calibration

Capture frames locally with `StoreLens.open_capture`; never expose resolved credentials.
Workers submit evidence and never resolve zones themselves.

## Objects

- **Zone:** canonical GeoJSON Polygon/MultiPolygon in map metres. Disconnected parts
  remain separate. Edits increment its revision.
- **Zone view:** one camera's visible/decision pixel polygons and point, bbox-overlap, or
  keypoint membership rule. Creating or editing it never changes the canonical zone.
- **Projection surface:** a named homography for a non-floor plane. Height is metadata;
  never subtract height from map Y.
- **Rich calibration:** provider-neutral 3x4 world-to-pixel matrix, explicit world frame
  and metric units, ground-plane Z, and optional intrinsics/extrinsics/distortion.

## Workflow

1. Inspect `get_store_map`, zones, views, surfaces, and calibrations. Confirm the physical
   footprint with the user.
2. Create or update canonical geometry in map metres. Use MultiPolygon for disconnected
   pieces; never connect them through unrelated space.
3. Calibrate the floor with at least four pixel/map control pairs, or use
   `import_calibration` for a validated 3x4 world calibration. Include verification points
   and inspect reprojection error.
4. Create a camera zone view and compare it with a locally captured frame. Use
   `unproject_points` only as a proposal.
5. If the verified camera view reveals physical space outside the canonical footprint,
   explicitly call `extend_zone_from_view`. Inspect the union and provenance. Never expand
   canonical geometry implicitly while editing a view.
6. Use a named projection surface for an elevated planar target.
7. For multiview, confirm every source uses one compatible metric world frame before
   creating the group.

The explicit extension provenance records source calibration, zone view, projection
surface, original pixels, projected map polygon, operation, and resulting zone revision.
Review stale-provenance warnings after any contributing definition changes.

A homography maps one plane; it does not reconstruct arbitrary 3D. Feet/bbox
bottom-center suit standing floor traffic. Non-planar localization needs a different
camera model and should not be approximated with pixel offsets.
