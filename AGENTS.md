# StoreLens agent operating manual

Load `skills/storelens-platform/SKILL.md` first for every StoreLens task, then the closest
task skill. StoreLens follows one rule: **observe locally, derive centrally**.

## Platform boundary

Local workers open sources, run models, track anonymous entities, and submit only
schema-v2 `detection`, `measurement`, or `state` observations. StoreLens owns projection,
canonical zone assignment, visits, dwell, occupancy, transitions, state intervals,
multiview association, saved queries, dashboards, and alerts.

Workers must never submit `zone_id`/`zone`, `zone_enter`, `zone_exit`, `zone_dwell`,
`state_change`, `count`, or calculated analytics. `entity_id` is an opaque tracker ID,
not a verified person identity. Do not invent cross-camera identity by joining IDs or
attributes.

## Source access

A source may use encrypted `storelens_managed` connection material or an
`external_secret` local reference. Ordinary discovery is redacted. Resolve credentials
only in an explicitly authorized local worker, use them in memory, and never place them
in observations, fused state, queries, dashboards, logs, code, or job metadata. StoreLens
does not proxy feeds or execute arbitrary worker scripts.

The guided demo is a narrow exception for bundled local sample media: it serves an
allowlisted NVIDIA asset set to the browser, replays a versioned numerical fixture with
`producer_kind=replay`, and creates no worker heartbeat. Never describe replay as live
inference. Demo workspaces are isolated; promote only the explicit setup allowlist.

## Observation envelope

Common fields are `schema_version: 2`, idempotent `observation_id`, `kind`, `timestamp`,
`source_id`, optional `sample_id`, worker/job IDs, confidence, opaque `entity_id`, honest
`identity_scope`, and attributes.

- `detection`: `entity_type`, optional label, and pixel evidence (`point_px`, corner-form
  `bbox_px`, keypoints, mask). StoreLens chooses explicit point, feet/ankles, then bbox
  bottom-center. Trusted non-camera producers may use `point_map`.
- `measurement`: name, numeric value, `gauge|delta|cumulative`, optional unit and label.
- `state`: name and observed label on every reading, including repeats.

For every processed detection frame, send zero or more detections and exactly one
`detection_frame_count` marker including zero. All rows share one opaque source-local
`sample_id` and exact timestamp. Prefer the SDK atomic sample builder. Partial or
count-mismatched samples do not replace current state. Legacy rows without `sample_id`
retain exact source/timestamp fallback. Missing data changes freshness, never scene value.

## Geometry and multiview

Canonical zones are metric GeoJSON Polygon/MultiPolygon. Zone views are camera-specific
and never mutate canonical geometry implicitly. Use `extend_zone_from_view` only after
validating the projected footprint; it records full revision provenance. Homographies map
planes, not arbitrary 3D.

Rich calibration imports accept validated 3x4 world-to-pixel matrices with metres,
explicit world axes, ground-plane Z, and optional camera metadata/verification points.
Only group compatible calibrated sources. Multiview consumes complete source samples and
uses geometry/time/trajectory/topology plus global assignment. It preserves source
evidence and produces anonymous active fused tracks with `known|partial|unknown` quality;
it is not biometric ReID.

## Agent workflow

1. Discover platform endpoints and current sources, geometry, calibrations, groups, jobs,
   current state, saved queries, dashboards, and alerts.
2. Load the relevant skill and reuse compatible definitions.
3. Register a job before posting; register the actual worker and heartbeat every 5-15s.
4. Run capture/inference locally, submit complete raw samples, and obey cooperative
   stop/restart. An external supervisor performs relaunch.
5. Verify raw rows, projection, zones, source samples, fused member evidence, and quality.
6. Preview deterministic analytics with `query_data`; agents never access raw SQL.
7. Save one canonical question with `create_saved_query`. Add it to a generated dashboard
   only when requested. Presentation changes do not duplicate a query.
8. Use query-backed alerts for dashboard-equivalent thresholds. Alerts are periodic,
   edge-triggered, cooldown-aware, and do not false-clear on unknown evidence.

## Skills

| Skill | Use |
|---|---|
| `storelens-platform` | required first guide |
| `source-onboarding` | source connections and credential safety |
| `detection-tracking` | presence, heatmaps, visits, dwell, flow |
| `measurement` | numeric readings |
| `state-observation` | categorical state samples |
| `geometry-calibration` | zones, views, surfaces, calibrations |
| `multiview` | calibrated groups and fused state |
| `analytics` | deterministic query preview/save |
| `generated-dashboard` | query-backed widgets |
| `alerts-workflows` | query and legacy alert rules |

REST/OpenAPI is authoritative. New workers use `POST /api/v1/observations/batch`; legacy
`/events` remains compatibility-only. The Python SDK is `sdk/python/storelens.py`.

Space and observation reinitialization are destructive, exact-confirmation operations.
Never invoke them without an explicit user request. A retained historical observation
belongs to its recorded `space_revision_id`; deleted-zone query references stay
unresolved and must not be matched by name.
