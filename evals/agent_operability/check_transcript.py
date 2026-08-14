#!/usr/bin/env python3
"""Check a recorded agent transcript against a StoreLens operability scenario.

    python evals/agent_operability/check_transcript.py <scenario> <transcript.json>

Exits 0 when the transcript satisfies every rule, 1 otherwise. Needs no running
StoreLens and no model: the rules are pure, so a session recorded from a real
Codex or Claude run can be graded after the fact.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import rules  # noqa: E402

SCENARIO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scenarios")


def available() -> list[str]:
    return sorted(name[:-5] for name in os.listdir(SCENARIO_DIR) if name.endswith(".json"))


def load(name: str) -> dict:
    path = os.path.join(SCENARIO_DIR, f"{name}.json")
    if not os.path.isfile(path):
        raise SystemExit(f"unknown scenario '{name}'; available: {available()}")
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__.strip())
        print(f"\nscenarios: {available()}")
        return 2
    scenario = load(argv[0])
    with open(argv[1], encoding="utf-8") as handle:
        transcript = json.load(handle)
    violations = rules.check(scenario, transcript)
    print(f"scenario: {scenario['name']} — {scenario['title']}")
    print(f"steps checked: {len(transcript.get('steps') or [])}")
    if not violations:
        print("PASS — no rule violations")
        return 0
    print(f"FAIL — {len(violations)} violation(s):")
    for violation in violations:
        turn = "-" if violation.turn is None else violation.turn
        print(f"  [{violation.rule}] turn {turn}: {violation.detail}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
