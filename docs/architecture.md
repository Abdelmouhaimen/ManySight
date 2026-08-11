# Architecture and current scope

StoreLens separates local observation from central spatial and temporal derivation.
The platform does not need to know which detector, tracker, classifier, or sensor
produced an observation.

## Components

1. **Sources** describe logical cameras, files, streams, or sensors. Connection
   configuration is either StoreLens-managed or resolved from an external secret.
2. **Local workers** open sources, run inference, track entities when useful, and
   submit direct observations through the REST API or Python SDK.
3. **The StoreLens API** persists observations in SQLite, enriches spatial evidence
   using the geometry active at ingestion, and exposes derived read models.
4. **The ManySight dashboard** is the bundled human interface for setup,
   observations, live views, saved analyses, workers, and alert review.
5. **The MCP server** adapts the REST API and agent playbooks for authorized coding
   agents. It is not a worker runtime or video-processing service.

## Data flow

Workers submit schema-v2 `detection`, `measurement`, or `state` observations. A
detection may carry a pixel point, bounding box, keypoints, or mask. StoreLens chooses
a representative point, applies the relevant floor or named-plane homography, and
assigns a physical zone. It records the source, worker, job, geometry revisions,
projection method, and assignment method with the stored row.

At query time StoreLens derives:

- current presence and spatial density from detections;
- visits, dwell, and transitions from ordered zone-assigned detections with opaque
  entity IDs;
- measurement series from instantaneous gauge, delta, or cumulative readings;
- state intervals, durations, and transitions from repeated state samples;
- saved analyses from a subject, measures, filters, and grouping;
- alerts from legacy rule kinds or a general condition on a saved analysis.

Legacy `/api/v1/events` data remains readable and accepted for compatibility. New
workers must use `/api/v1/observations/batch`.

## Geometry model

The workspace floor plan and zones use map metres. A source can have a floor-plane
homography based on four or more pixel-to-map correspondences. A named projection
surface models another planar target, such as a shelf or tabletop. A zone view stores
how one camera sees a physical zone and can use point, bounding-box-overlap, or
keypoint membership.

A homography maps one two-dimensional plane to another. It does not reconstruct
arbitrary three-dimensional geometry, infer height, or make non-planar objects metric.
Geometry changes affect future ingestion; historical rows retain the revisions that
were used when they were recorded.

## Identity and privacy boundary

`entity_id` is an opaque tracker identifier, not a verified person identity.
`identity_scope` declares whether the identifier is valid for one worker run, one
source, or the workspace. StoreLens does not join identities across cameras by
similarity and does not require face embeddings or biometric templates.

Workers should submit only the evidence needed for the intended analysis. Camera
frames remain on the worker device unless an operator explicitly chooses to retain
them elsewhere.

## Current operational scope

- The implementation uses one SQLite database and a single workspace/store record.
- API-key authentication is optional and is not a user/account or role system.
- Managed camera credentials are encrypted at rest, but operators must supply and
  protect the encryption and resolution keys.
- Worker stop and restart are cooperative. StoreLens returns desired state on a
  heartbeat; an external supervisor is responsible for relaunching a process.
- Source reachability is evaluated on the worker machine, not by the StoreLens server.
- Live and analytical results depend on model quality, calibration quality, sampling
  rate, and the identity guarantees of the worker.
- The dashboard can create legacy alert-rule forms; the general
  `analysis_condition` rule is currently available through REST and MCP.

These limits are part of the current design rather than guarantees of production
readiness. See [Development and deployment](development.md) for configuration and
[Workers and observations](workers.md) for the ingestion contract.
