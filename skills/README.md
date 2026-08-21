# ManySight skills

Job-shaped playbooks that teach a coding agent how to operate ManySight correctly. Each
folder has a `SKILL.md` stating when to use it, the invariants that decide whether an
implementation is right, and the curated tools that implement it.

They are discoverable three ways:

- **In-repo**: compatible agents read the [agent operating manual](../docs/agents/AGENTS.md), which indexes this folder.
- **Over MCP**: `get_skill(name)` serves these files, so an agent can follow them even when
  working outside this repository.
- **By job**: `list_workflows()` / `get_workflow(name)` route a task ("create an occupancy
  alert") to the right skills without guessing filenames.

| skill | use |
|---|---|
| [manysight-core](manysight-core/SKILL.md) | **load first** — platform boundary, atomic samples, identity, geometry, quality, where authority lives |
| [sources-and-cameras](sources-and-cameras/SKILL.md) | onboarding cameras/streams/sensors, credential safety, inspecting a camera view, freshness |
| [geometry-and-zones](geometry-and-zones/SKILL.md) | canonical zones, camera zone views, projection surfaces, calibration, preview→approve→commit |
| [perception-workers](perception-workers/SKILL.md) | detection, measurement, and state workers — reuse first, current contract, tracking frame rate, GPU/CUDA readiness, verification |
| [multiview-fusion](multiview-fusion/SKILL.md) | calibrated camera groups, anonymous fused tracks, cross-camera counting |
| [queries-dashboards-alerts](queries-dashboards-alerts/SKILL.md) | deterministic questions, query-backed widgets, exact comparison operators, quality-aware alerts |
| [guided-demo](guided-demo/SKILL.md) | the isolated playable demo, its boundaries, and its promotion rules |

Contract reminder — **observe locally, derive centrally**: workers submit only three raw
observation kinds (`detection`, `measurement`, `state`) — never a zone ID, a computed
dwell/occupancy/transition, or a state change. One atomic `DetectionSample` per processed
frame; `detections=[]` is a known zero and no fresh sample means unknown, not zero.
`entity_id` is an opaque source-local tracker ID, not an identity.

The current MCP tools, `GET /api/v1/observations/contract`, `get_worker_recipe()`, and
`/openapi.json` are authoritative. An example or demo script found on disk is not.
