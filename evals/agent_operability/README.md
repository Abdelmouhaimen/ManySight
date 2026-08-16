# Agent-operability evaluation

Regression scenarios for a question this repository cannot answer with unit tests
alone: *can a coding agent operate ManySight correctly without the user teaching
it the architecture?*

Each scenario is derived from a real failure. The first one,
[`aisle-04-occupancy-alert`](scenarios/aisle-04-occupancy-alert.json), is a
transcription of an actual Codex session that needed four user corrections before
it produced a good result.

## What a scenario contains

| field | meaning |
|---|---|
| `initial_workspace` | the state the harness builds before turn 0 |
| `turns[].user` | what the user says |
| `turns[].expect` | actions that turn must and must not contain, and how it must end |
| `order_required` | pairs that must happen in order across the whole session |
| `actions_forbidden_overall` | actions that must never appear |
| `action_arguments` | argument assertions, e.g. the alert operator must be `>` |
| `polygon_excludes` | pixel points a proposed polygon must not contain |
| `expected_final_resources` | what must exist when the session ends |
| `reference_transcript` | the deterministic correct path, used by the test suite |

`$Camera 3`, `$zone`, `$group`, and `$query` are placeholders the harness
substitutes with the IDs it created. Rule checking runs against the unsubstituted
transcript, so both the spec and the transcript stay readable.

## How it runs

`tests/test_agent_operability.py` does three things per scenario:

1. **Self-consistency** — the reference transcript satisfies the scenario's own
   rules. A scenario whose golden path violates its own spec is a broken scenario.
2. **Rule sensitivity** — deliberately broken transcripts (asking for coordinates
   before looking at a camera, committing geometry before approval, using `>=`
   for "more than") produce the expected violations. A checker that passes
   everything is worthless.
3. **Real execution** — the reference transcript is executed against the real
   FastAPI app, and `expected_final_resources` is asserted against what the
   platform actually stored, including whether the alert really fires at 3 and
   not at 2.

## Checking a recorded agent run

`rules.py` is pure — no HTTP, no database, no model — so a transcript recorded
from a real Codex or Claude session can be checked without a deployment:

```bash
python evals/agent_operability/check_transcript.py \
    aisle-04-occupancy-alert my-recorded-run.json
```

The transcript format is `{"steps": [{"turn": 0, "action": "inspect_workspace",
"args": {...}}, ...]}` where `action` is a curated MCP tool name or one of the
meta-actions `ask_user`, `read_local_file`, `run_shell`.

## Honest limits

- **No LLM runs in CI.** These scenarios validate the deterministic route: that
  the tools, workflows, and skills make the correct path available and that the
  platform enforces the invariants. They do not prove a given model will choose
  it. Use `check_transcript.py` on a real recorded run for that.
- Some `§30`-style failures are checked by proxy. "Treated a stale repository
  script as the protocol" is not directly observable, so the scenario requires
  `get_worker_recipe` before any `run_shell` instead.
- `run_shell` and `read_local_file` are recorded as opaque meta-actions. The
  harness does not execute a real detector; the reference execution submits
  synthetic detection samples through the real ingestion path in place of the
  worker the agent would have started.
