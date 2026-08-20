---
name: geometry-and-zones
description: Use for canonical zones, camera zone views, projection surfaces, and calibration. Covers the full flow from "the user named a region that does not exist yet" through camera inspection, projection preview, user approval, and one canonical commit.
---

# Geometry and zones

Load [`manysight-core`](../manysight-core/SKILL.md) first.

## The four objects

- **Zone** — canonical GeoJSON Polygon/MultiPolygon in map metres. The physical footprint.
  Disconnected parts stay separate components. Edits increment its revision.
- **ZoneView** — one camera's visible/decision **pixel** polygons plus a membership rule
  (`point`, `bbox_overlap`, `keypoints_inside`). Creating or editing it never changes the
  canonical zone.
- **Projection surface** — a named homography for a non-floor plane. Height is metadata;
  never subtract height from map Y.
- **Rich calibration** — provider-neutral 3x4 world-to-pixel matrix with explicit world
  frame, metric units, ground-plane Z, and optional intrinsics/extrinsics/distortion.

A ZoneView is **not** a zone:

```text
Camera 3 image polygon
        │ project through Camera 3 calibration
        ▼
physical contribution ─┐
                       ├──► ONE canonical Aisle 04
physical contribution ─┘
        ▲
        │ project through Camera 4 calibration
Camera 4 image polygon
```

## When a named region has no geometry yet

This is the common request — "alert me about Aisle 04" when no Aisle 04 exists. Do **not**
open by asking the user for a polygon.

1. `inspect_workspace()` — confirm the zone really is missing and the map exists.
2. Identify which **calibrated** sources plausibly see the region.
3. `plan_frame_capture(source_id)` for each candidate; run it and look at the images.
4. Propose image-space polygons covering **the walkable floor only**. Exclude shelving,
   racks, pallets, and anything else standing on the floor — a rack's pixels project to
   floor coordinates it does not occupy.
5. `preview_zone(views=[...], zone_name="...")`. Nothing is persisted. Check each
   projected polygon's validity and area, the unioned canonical preview, and any
   calibration warnings.
6. Show the preview to the user and ask for approval or correction.
7. On any correction — "only the floor hallway, not objects", "try again" — adjust the
   pixel polygons and `preview_zone` again. Still nothing persisted.
8. Only after explicit approval: `commit_zone(views=[...], approved=True, zone_name="...")`.
   That creates one canonical zone plus one ZoneView per contributing camera, unions each
   projected contribution, and records full provenance.
9. Verify the committed zone's component count, area, and `geometry_provenance`.

Cameras that cannot see the region get **no** ZoneView. Never invent a polygon to make
coverage look complete. Never nudge coordinates to look neater.

Subjective geometry is never persisted before approval. Objective corrections (a wrong
source id, an invalid polygon) are yours to fix; where the region *is* belongs to the user.

## Calibration

Calibrate the floor from at least four pixel/map control pairs, or import a validated 3x4
world calibration with verification points and inspect the reprojection error. Only group
compatible calibrated sources for fusion — do not mix rich and planar-only sources in one
group.

A homography maps one plane; it does not reconstruct arbitrary 3D. Feet or bbox
bottom-centre suit standing floor traffic. Non-planar localization needs a different camera
model and must not be approximated with pixel offsets.

## Provenance and staleness

Every projection records the source calibration revision, zone view id and revision,
projection surface revision, original pixels, projected map polygon, operation, and
resulting zone revision. Replacing a floor calibration automatically reprojects zones
that remain fully derived from stored camera-pixel evidence and records a new provenance
revision. It does not overwrite map-authored or subsequently hand-edited zones. After
changing a view or projection surface, re-read the zone and review stale-provenance
warnings; those changes still require an explicit canonical extension.

Retained observations belong to their recorded `space_revision_id`. A deleted zone's
references stay unresolved and must never be re-matched by name.
