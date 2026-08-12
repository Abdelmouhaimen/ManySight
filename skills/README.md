# StoreLens skills

Playbooks that teach a coding agent how to configure workers and deterministic queries.
Each folder has a `SKILL.md`: when to use it, the exact MCP/REST calls, observation
conventions, and a runnable worker-script template.

They are discoverable two ways:
- **In-repo**: compatible agents read `AGENTS.md`, which indexes this folder.
- **Over MCP**: the `list_skills` / `get_skill` tools serve these files, so an agent can
  follow them even when working outside this repository.

| skill | purpose |
|---|---|
| [storelens-platform](storelens-platform/SKILL.md) | default guide: MCP/REST roles, the observation contract, worker lifecycle, and safe defaults |
| [detection-tracking](detection-tracking/SKILL.md) | people/object positions, spatial heatmaps, time in zones, and dwell — one worker contract, several questions |
| [measurement](measurement/SKILL.md) | numeric readings over time — population counts, queue length, any classifier output |
| [state-observation](state-observation/SKILL.md) | equipment states (fridge open/closed) and their durations |
| [geometry-calibration](geometry-calibration/SKILL.md) | zones, zone views, projection surfaces, camera calibration |
| [source-onboarding](source-onboarding/SKILL.md) | managed/external source configuration and credential safety |
| [multiview](multiview/SKILL.md) | calibrated camera groups and anonymous fused current state |
| [alerts-workflows](alerts-workflows/SKILL.md) | threshold alerts, loitering, webhooks/n8n |
| [analytics](analytics/SKILL.md) | preview and save deterministic data queries |
| [generated-dashboard](generated-dashboard/SKILL.md) | create safe query-backed dashboard widgets |

Contract reminder — **observe locally, derive centrally**: workers submit only three raw
observation kinds (`detection`, `measurement`, `state`) — never a zone ID, a computed
dwell/occupancy/transition, or a state change. StoreLens derives everything else. Workers
preserve bbox/keypoint/mask evidence, register a concrete worker instance, and heartbeat
while alive. Save a verified question with `create_saved_query`; add it to a generated
dashboard only when the user wants a persistent view.

An MCP-only agent should load `storelens-platform` before any task-specific skill.
