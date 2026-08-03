---
name: geometry-calibration
description: Use when setting up or fixing zones, camera calibration, zone views, or projection surfaces — "draw a zone", "the camera isn't calibrated", "this needs a mattress/table/shelf plane", or any question about why a detection isn't landing in the right zone. Also the reference for how StoreLens turns pixel evidence into a map point and a zone.
---

# Geometry & calibration

A physical zone and its appearance in one camera are deliberately separate concepts.
Workers never resolve any of this themselves — they submit pixel evidence and StoreLens
does the rest — but an agent configuring the space needs to understand the model to set
it up correctly.

## The three geometry objects

1. **Zone** — the canonical footprint in map metres (`create_zone`/`update_zone`). This
   is what filters, alerts, and analyses reference. Editing it increments its `revision`;
   historical observations keep the revision that was active when they were ingested.
2. **Zone view** — belongs to one zone *and* one camera (`create_zone_view`). Stores the
   visible outer polygon, an inset detection ROI, and a membership rule: `point` (a
   representative point inside the ROI), `bbox_overlap` (a configured fraction of the
   bbox inside the ROI), or `keypoints_inside` (a fraction of keypoints inside, with a
   minimum count). This is how a camera-specific decision region maps onto the global zone.
3. **Projection surface** — a named additional pixel→map homography for a plane other
   than the floor (mattress, table, shelf, conveyor, platform), computed from at least
   four `{px,map}` point pairs (`create_projection_surface`). `height_m` is descriptive
   metadata only — **never** subtract it from map Y; a 2D homography has no vertical axis.

## Steps to set up a new zone from a camera view

1. Capture and inspect a frame directly on the worker device. Never upload the frame
   unless the user explicitly chooses to retain visual evidence.
2. Call `get_store_map()` for the current global footprint and confirm it with the user
   before creating/updating it.
3. Propose the zone: either `create_zone(name, ztype, polygon_map=[...])` in map metres,
   or `create_zone(name, ztype, polygon_px=[...], source_id=...)` on the calibrated floor
   plane (StoreLens projects it — 409 if the source isn't calibrated yet; ask the user to
   calibrate, or fall back to a map-metre polygon).
4. If the camera needs its own visible/inset polygon or a non-default membership rule,
   create a **zone view**: `create_zone_view(zone_id, source_id, outer_polygon_px,
   detection_polygon_px?, projection_surface_id?, membership_rule, threshold?, min_keypoints?)`.
   Use `unproject_points` to propose camera pixels from the map footprint, then check the
   result against a frame captured on the worker device.
5. For an elevated planar target (mattress, table, shelf, conveyor), create a
   **projection surface** from ≥4 matching `{px,map}` points first, then attach its id to
   the zone view (or pass it per-observation as `projection_surface_id`).
6. `ztype` ("restricted", "queue", "checkout", ...) is a semantic label only — it carries
   no behavior. What happens when someone enters (alerts, review signals) is configured
   separately with `create_alert_rule`; a worker never needs to know what a zone means.

## Calibrating a camera's floor plane

`PUT /sources/{id}/calibration` (or the dashboard's Setup → Space & zones → ⌗) with at
least 4 `{px, map}` point pairs. Calibration increments `calibration_revision`; historical
observations keep the revision active when they were projected.

## Choosing geometry by what the point physically touches

- **Standing/walking on the floor:** use feet/bbox-bottom-center and the floor calibration.
- **Lying or sitting on a known planar surface:** define a named projection surface,
  attach it to the zone view, and submit a representative point on that plane (e.g.
  hip/torso center) or let the ROI assign the zone from bbox/keypoints.
- **Presence in a visible region with no reliable single point:** use a zone view.
  `point` tests one representative point, `bbox_overlap` requires a configured fraction of
  the box in the inset ROI, and `keypoints_inside` combines an inside fraction with `min_keypoints`.
- **Map footprint to camera proposal:** call `unproject_points` with the selected surface,
  then inset the returned polygon and confirm it against a locally captured frame.
- **Non-planar 3D requirement:** do not improvise a pixel or map offset. Explain that
  camera intrinsics/extrinsics and ray–plane or 3D reconstruction are required, and that
  the current plane-homography model doesn't cover it.

Use `get_store_map`, `list_projection_surfaces`, and `list_zone_views` to reuse current
geometry rather than guessing. Update definitions in place so their revisions increment;
historical observations retain the revisions that produced them — never silently
reinterpret past evidence after a geometry edit.
