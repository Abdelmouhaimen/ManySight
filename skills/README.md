# StoreLens skills

Playbooks that teach a coding agent (Codex) how to run analyses on the platform.
Each folder has a `SKILL.md`: when to use it, the exact MCP/REST calls, event
conventions, and a runnable worker-script template.

They are discoverable two ways:
- **In-repo**: Codex reads `AGENTS.md`, which indexes this folder.
- **Over MCP**: the `list_skills` / `get_skill` tools serve these files, so Codex can
  follow them even when working outside this repository.

| skill | purpose |
|---|---|
| [storelens-platform](storelens-platform/SKILL.md) | default guide: MCP/REST roles, zone views and planes, worker lifecycle, event provenance, and safe defaults |
| [heatmap](heatmap/SKILL.md) | spatial traffic heatmaps on the floor plan |
| [dwell-time](dwell-time/SKILL.md) | time spent in zones, grouped by attributes |
| [state-monitoring](state-monitoring/SKILL.md) | equipment states (fridge open/closed) + durations |
| [alerts-workflows](alerts-workflows/SKILL.md) | threshold alerts, loitering, webhooks/n8n |
| [insights](insights/SKILL.md) | publish results as registered dashboard insight cards |

Contract reminder: workers post **raw observations** (detections, enter/exit pairs,
label-only state changes, per-frame counts) — the platform derives dwell, durations,
and every insight. They preserve bbox/keypoint/mask evidence, register a concrete worker
instance, and heartbeat while alive. Finish an analysis by registering it with
`register_insight`.

An MCP-only agent should load `storelens-platform` before any task-specific skill.
