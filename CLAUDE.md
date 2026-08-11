# Claude Code repository guidance

Read [`AGENTS.md`](AGENTS.md) before changing StoreLens. It is the canonical
agent-facing operating manual for the current observation contract, source access,
geometry, worker lifecycle, analytics, alerts, and repository commands.

For every StoreLens task, load
[`skills/storelens-platform/SKILL.md`](skills/storelens-platform/SKILL.md) first, then
the closest task-specific playbook from [`skills/`](skills/README.md).

Human-facing project documentation begins in [`README.md`](README.md). Do not copy
agent instructions into public user documentation, expose source credentials, or
reintroduce legacy worker-derived zones, dwell, occupancy, transitions, or state
changes.
