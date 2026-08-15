# Geometry and calibration

ManySight uses one metric map frame for canonical physical geometry. Workers submit
camera evidence; the platform selects the relevant plane, projects the representative
point, assigns a zone, and stores the definition revisions used.

## Defining a zone from camera evidence

When a named physical region has no canonical geometry yet, the polygon is subjective and
must be approved before it is stored. Two endpoints make that safe:

- `POST /api/v1/agent/zone-preview` projects proposed camera-space polygons onto the map
  and returns each projected footprint with validity, area, and calibration revision, plus
  the unioned canonical preview and any calibration warnings. **It persists nothing** and
  may be called repeatedly as the user corrects the proposal.
- `POST /api/v1/agent/zone-commit` requires `approved=true`, then runs the same low-level
  sequence described below: create the canonical zone from the first contribution, create
  one zone view per contributing camera, and union the remaining contributions with
  explicit extension calls so every step records provenance.

The result is one canonical zone with one zone view per contributing camera. Cameras that
cannot see the region get no zone view. `GET /api/v1/agent/sources/{id}/frame-capture-plan`
supplies the local capture plan and pixel-coordinate context needed to propose polygons
from a real image rather than from guessed coordinates; ManySight never returns camera
pixels itself. See [the agent operating surface](agent-surface.md).

## Canonical zones and camera views

A zone is GeoJSON Polygon or MultiPolygon in map metres. MultiPolygon supports genuinely
disconnected pieces without connecting them through unrelated space. `polygon` remains
in responses as a compatibility exterior, while `geometry` is authoritative.

A zone view belongs to one source and one canonical zone. It stores visible and decision
polygons in source pixels, an optional projection surface, and a point/bbox/keypoint
membership rule. Creating or editing a view does not change the canonical zone.

`POST /api/v1/zone-views/{view_id}/extend-zone` is the explicit extension operation. It
projects the selected outer or detection polygon, unions it into canonical geometry, and
records source calibration revision, zone-view revision, surface revision, original
pixels, projected map points, and resulting zone revision. Review stale-provenance
warnings after a calibration or view changes.

## Calibration forms

The interactive Setup → Cameras workflow (presented to users as *Camera calibration*)
accepts at least four pixel/map correspondences for a floor homography. `POST /api/v1/calibrations/import` accepts richer provider-neutral
world calibration:

- a rank-3 3x4 world-to-pixel projection matrix;
- metres as world units;
- a named world frame with explicit axes;
- an optional affine provider-world-to-ManySight-map transform (the original matrix is preserved);
- ground-plane Z;
- optional frame dimensions, distortion, intrinsics, extrinsics, and verification points.

The importer supports `generic`, `nvidia_mv3dt`, and `nvidia_amc` provenance labels but
keeps the stored model provider-neutral. For ground Z, it derives world-to-pixel and
pixel-to-world homographies. Verification points report reprojection error rather than
silently claiming a calibration is valid.

Homographies model planes. They do not provide arbitrary 3D reconstruction, vertical
position, or a correct map point for a torso projected through a floor plane. Use feet
or bbox bottom-center for standing people and named planes for elevated surfaces.

## Relevant API

- `GET /api/v1/zones/{id}`: canonical geometry and extension provenance.
- `GET /api/v1/zone-views`: camera-specific decision geometry.
- `POST /api/v1/zone-views/{id}/extend-zone`: explicit canonical union.
- `GET /api/v1/calibrations`: rich calibration records.
- `POST /api/v1/calibrations/import`: world calibration import.
- `POST /api/v1/sources/{id}/project` and `/unproject`: plane projection checks.

The guided demo exercises this same path: it imports the NVIDIA 3x4 matrices with an
explicit provider-world-to-map transform, projects a predetermined camera polygon to
trace Aisle 04 in cameras 3 and 4, project both views onto the floor, explicitly union
their overlapping contributions into one canonical metric footprint, and store per-camera decision
views. Those pixels are evidence
for a plane projection; they are not a 3D reconstruction.
