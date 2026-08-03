# StoreLens skills

Playbooks that teach a coding agent (Codex) how to run analyses on the platform.
Each folder has a `SKILL.md`: when to use it, the exact MCP/REST calls, observation
conventions, and a runnable worker-script template.

They are discoverable two ways:
- **In-repo**: Codex reads `AGENTS.md`, which indexes this folder.
- **Over MCP**: the `list_skills` / `get_skill` tools serve these files, so Codex can
  follow them even when working outside this repository.

| skill | purpose |
|---|---|
| [storelens-platform](storelens-platform/SKILL.md) | default guide: MCP/REST roles, the observation contract, worker lifecycle, and safe defaults |
| [detection-tracking](detection-tracking/SKILL.md) | people/object positions, spatial heatmaps, time in zones, and dwell — one worker contract, several questions |
| [measurement](measurement/SKILL.md) | numeric readings over time — population counts, queue length, any classifier output |
| [state-observation](state-observation/SKILL.md) | equipment states (fridge open/closed) and their durations |
| [geometry-calibration](geometry-calibration/SKILL.md) | zones, zone views, projection surfaces, camera calibration |
| [alerts-workflows](alerts-workflows/SKILL.md) | threshold alerts, loitering, webhooks/n8n |
| [analytics](analytics/SKILL.md) | publish results as saved analyses on the dashboard |

Contract reminder — **observe locally, derive centrally**: workers submit only three raw
observation kinds (`detection`, `measurement`, `state`) — never a zone ID, a computed
dwell/occupancy/transition, or a state change. StoreLens derives everything else. Workers
preserve bbox/keypoint/mask evidence, register a concrete worker instance, and heartbeat
while alive. Finish an analysis by saving it with `create_analysis`.

An MCP-only agent should load `storelens-platform` before any task-specific skill.
