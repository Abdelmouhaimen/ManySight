"""Deterministic rule checker for StoreLens agent-operability scenarios.

A scenario is a specification, not a script: initial workspace, the user's turns,
which semantic actions each turn must and must not contain, and what must exist
at the end. This module turns any transcript — one produced by the scripted
reference path in the test suite, or one recorded from a real Codex/Claude run —
into a list of violations.

Deliberately pure: no HTTP, no database, no model, no third-party imports. It can
run in CI on a recorded transcript without a StoreLens deployment.

Transcript shape:

    {"steps": [{"turn": 0, "action": "inspect_workspace", "args": {...}}, ...]}

`action` is a curated MCP tool name, or one of the meta-actions:
  ask_user           the agent handed control back for a decision
  read_local_file    the agent read a file from disk as if it were documentation
  run_shell          the agent ran something unrelated to perception in its shell
  run_worker         the agent launched a perception worker locally
"""
from __future__ import annotations

META_ACTIONS = {"ask_user", "read_local_file", "run_shell", "run_worker"}


class Violation:
    """One failed rule. `rule` is stable enough to assert on in tests."""

    __slots__ = ("rule", "turn", "detail")

    def __init__(self, rule: str, turn: int | None, detail: str):
        self.rule, self.turn, self.detail = rule, turn, detail

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Violation({self.rule!r}, turn={self.turn}, {self.detail!r})"

    def __eq__(self, other) -> bool:
        return (isinstance(other, Violation) and other.rule == self.rule
                and other.turn == self.turn and other.detail == self.detail)

    def as_dict(self) -> dict:
        return {"rule": self.rule, "turn": self.turn, "detail": self.detail}


def _steps(transcript: dict) -> list[dict]:
    return list(transcript.get("steps") or [])


def _turn_steps(transcript: dict, turn: int) -> list[dict]:
    return [step for step in _steps(transcript) if int(step.get("turn", -1)) == turn]


def _first_index(steps: list[dict], action: str) -> int | None:
    for index, step in enumerate(steps):
        if step.get("action") == action:
            return index
    return None


def point_in_polygon(point: tuple[float, float], polygon: list[dict]) -> bool:
    """Ray-cast membership. Kept local so this module stays dependency-free."""
    x, y = point
    inside = False
    count = len(polygon)
    for index in range(count):
        x0, y0 = float(polygon[index]["x"]), float(polygon[index]["y"])
        x1, y1 = float(polygon[(index + 1) % count]["x"]), float(polygon[(index + 1) % count]["y"])
        if (y0 > y) != (y1 > y):
            crossing = x0 + (y - y0) * (x1 - x0) / (y1 - y0)
            if x < crossing:
                inside = not inside
    return inside


def _matches(args: dict, expected: dict) -> bool:
    for key, want in expected.items():
        if key not in args:
            return False
        got = args[key]
        if isinstance(want, (int, float)) and isinstance(got, (int, float)):
            if float(got) != float(want):
                return False
        elif got != want:
            return False
    return True


def check(scenario: dict, transcript: dict) -> list[Violation]:
    """Every rule the scenario declares, evaluated against one transcript."""
    violations: list[Violation] = []
    steps = _steps(transcript)
    actions = [step.get("action") for step in steps]

    for index, turn in enumerate(scenario.get("turns") or []):
        expect = turn.get("expect") or {}
        present = [step.get("action") for step in _turn_steps(transcript, index)]
        for required in expect.get("actions_required") or []:
            if required not in present:
                violations.append(Violation(
                    "actions_required", index,
                    f"turn {index} must call {required}; it called {present}"))
        for forbidden in expect.get("actions_forbidden") or []:
            if forbidden in present:
                violations.append(Violation(
                    "actions_forbidden", index,
                    f"turn {index} must not call {forbidden}"))
        for action, minimum in (expect.get("action_counts_min") or {}).items():
            seen = present.count(action)
            if seen < int(minimum):
                violations.append(Violation(
                    "action_counts_min", index,
                    f"turn {index} must call {action} at least {minimum} times "
                    f"(e.g. verify after acting, not only before); it called it {seen} times"))
        ends_with = expect.get("ends_with")
        if ends_with and (not present or present[-1] != ends_with):
            violations.append(Violation(
                "ends_with", index,
                f"turn {index} must end with {ends_with}; it ended with "
                f"{present[-1] if present else 'nothing'}"))

    for earlier, later in scenario.get("order_required") or []:
        first_later = _first_index(steps, later)
        first_earlier = _first_index(steps, earlier)
        if first_later is None:
            continue
        if first_earlier is None or first_earlier > first_later:
            violations.append(Violation(
                "order_required", steps[first_later].get("turn"),
                f"{earlier} must happen before the first {later}"))

    for forbidden in scenario.get("actions_forbidden_overall") or []:
        if forbidden in actions:
            index = _first_index(steps, forbidden)
            violations.append(Violation(
                "actions_forbidden_overall", steps[index].get("turn"),
                f"{forbidden} must never be called in this scenario"))

    for rule in scenario.get("action_arguments") or []:
        action, want = rule["action"], rule.get("where") or {}
        candidates = [step for step in steps if step.get("action") == action]
        if rule.get("turn") is not None:
            candidates = [step for step in candidates
                          if int(step.get("turn", -1)) == int(rule["turn"])]
        if not candidates:
            violations.append(Violation(
                "action_arguments", rule.get("turn"),
                f"{action} was never called, so {want} could not be checked"))
            continue
        if not any(_matches(step.get("args") or {}, want) for step in candidates):
            got = [step.get("args") for step in candidates]
            violations.append(Violation(
                "action_arguments", rule.get("turn"),
                f"no {action} call matched {want}; saw {got}"))

    for rule in scenario.get("polygon_excludes") or []:
        action, source_id_key = rule["action"], rule.get("source_key", "source_id")
        forbidden_points = [tuple(point) for point in rule["must_exclude_px"]]
        candidates = [step for step in steps if step.get("action") == action]
        if rule.get("turn") is not None:
            candidates = [step for step in candidates
                          if int(step.get("turn", -1)) == int(rule["turn"])]
        if not candidates:
            violations.append(Violation(
                "polygon_excludes", rule.get("turn"),
                f"{action} was never called, so floor-only geometry was not demonstrated"))
            continue
        for step in candidates:
            for view in (step.get("args") or {}).get("views") or []:
                if rule.get("source") is not None and view.get(source_id_key) != rule["source"]:
                    continue
                for point in forbidden_points:
                    if point_in_polygon(point, view.get("polygon_px") or []):
                        violations.append(Violation(
                            "polygon_excludes", step.get("turn"),
                            f"{action} polygon for source {view.get(source_id_key)} contains "
                            f"excluded point {point} (the user asked for floor only)"))

    return violations


def check_final_resources(scenario: dict, observed: dict) -> list[Violation]:
    """Compare the workspace an execution actually produced with the expectation."""
    violations: list[Violation] = []
    expected = scenario.get("expected_final_resources") or {}
    for key, want in expected.items():
        got = observed.get(key)
        if isinstance(want, dict) and isinstance(got, dict):
            if not _matches(got, want):
                violations.append(Violation(
                    "expected_final_resources", None, f"{key}: expected {want}, got {got}"))
        elif got != want:
            violations.append(Violation(
                "expected_final_resources", None, f"{key}: expected {want}, got {got}"))
    return violations
