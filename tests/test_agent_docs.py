"""Agent-facing documentation is part of the product surface, not decoration.

An agent reads `skills/`, `docs/agents/AGENTS.md`, and the workflow registry instead of the
source, so a stale sentence there causes exactly the failures this milestone
exists to prevent. These tests assert the invariants are present, that retired
guidance is gone, and that every relative link between agent docs resolves.
"""
import os
import re

import pytest

from server.services import agent_workflows

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILLS_DIR = os.path.join(ROOT, "skills")
EXPECTED_SKILLS = {
    "manysight-core", "sources-and-cameras", "geometry-and-zones", "perception-workers",
    "multiview-fusion", "queries-dashboards-alerts", "guided-demo",
}
# Consolidated away by this milestone. Their content moved into the skills above;
# leaving a stale copy behind is how an agent ends up following two contracts.
RETIRED_SKILLS = {
    "manysight-platform", "detection-tracking", "measurement", "state-observation",
    "geometry-calibration", "source-onboarding", "multiview", "analytics",
    "generated-dashboard", "alerts-workflows",
}
MARKDOWN_FILES = [
    os.path.join(base, name)
    for base, dirs, files in os.walk(ROOT)
    for name in files
    if name.endswith(".md")
    and not any(part in base.split(os.sep)
                for part in ("node_modules", "dist", ".git", "data", ".pytest_cache"))
]


def read(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def skill(name: str) -> str:
    return read(os.path.join(SKILLS_DIR, name, "SKILL.md"))


def flat(name: str) -> str:
    """Markdown is hard-wrapped and sometimes quoted; normalise before matching.

    Strips blockquote markers and collapses whitespace so an assertion tests the
    sentence a reader sees, not how it happens to be wrapped.
    """
    lines = [re.sub(r"^\s*>\s?", "", line) for line in skill(name).splitlines()]
    return " ".join(" ".join(lines).split()).lower()


# ---------------------------------------------------------------------------
# skill organisation
# ---------------------------------------------------------------------------

def test_skills_are_consolidated_around_jobs():
    present = {entry for entry in os.listdir(SKILLS_DIR)
               if os.path.isdir(os.path.join(SKILLS_DIR, entry))}
    assert present == EXPECTED_SKILLS
    assert not present & RETIRED_SKILLS
    for name in EXPECTED_SKILLS:
        assert os.path.isfile(os.path.join(SKILLS_DIR, name, "SKILL.md"))


@pytest.mark.parametrize("name", sorted(EXPECTED_SKILLS))
def test_every_skill_declares_its_name_and_when_to_use_it(name):
    content = skill(name)
    assert content.startswith("---\n"), "front matter is how skills are indexed"
    front = content.split("---")[1]
    assert f"name: {name}" in front, "the declared name must match the folder"
    description = next(line for line in front.splitlines() if line.startswith("description:"))
    assert len(description) > 60, "a description must say when to reach for the skill"
    assert content.count("# ") >= 1


@pytest.mark.parametrize("name", sorted(EXPECTED_SKILLS - {"manysight-core"}))
def test_task_skills_point_back_at_the_core_skill(name):
    assert "manysight-core" in skill(name), \
        "every task skill must route the agent to the invariants first"


def test_core_skill_teaches_the_non_negotiable_invariants():
    content = flat("manysight-core")
    for phrase in (
        "observe locally, derive centrally",
        "`detections=[]` is a **known explicit zero**",
        "no completed fresh sample means unknown or stale — not zero",
        "never fake a zero-confidence detection",
        "opaque **source-local tracker id**",
        "not identity and not appearance reid",
        "one physical region is one canonical zone, never one per camera",
        "do not treat an arbitrary repository script as manysight protocol documentation",
        "agents never receive raw sql",
        "destructive, exact-confirmation operations",
    ):
        assert phrase in content, f"manysight-core must state: {phrase}"
    for forbidden in ("zone_enter", "zone_exit", "zone_dwell", "state_change"):
        assert forbidden in content, "the forbidden worker output must be enumerated"
    assert len(content.split()) < 1200, "the core skill must stay short enough to always load"


def test_perception_skill_prevents_the_stale_script_failure():
    content = flat("perception-workers")
    assert "inspect_perception" in content and "reuse" in content
    assert "get_worker_recipe" in content
    assert "may predate the current api" in content
    assert "if a file on disk disagrees with the recipe, the recipe is right" in content
    assert "detections=[] is a real observed zero" in content
    assert "must not author that internal completion concept" in content
    # Three rates, and a real floor for the one tracking depends on.
    assert "process at least 15 fps per camera" in content
    assert "30 fps or source-native" in content
    assert "there is no globally correct submission rate" in content
    assert "gate submission; do not slow the tracker" in content
    assert "hard-code a sleep" in content
    # Hardware is the agent's own machine to inspect, and CPU is not a failure.
    assert "probe_perception_runtime" in content
    assert "conda" in content, "reuse an existing local environment"
    assert "cpu is a supported fallback" in content
    assert "never makes a camera unusable" in content
    # Verification, not assumption.
    assert "claiming a worker is healthy without checking its heartbeat" in content
    assert "occasional samples are not health" in content


def test_geometry_skill_teaches_the_preview_approve_commit_flow():
    content = flat("geometry-and-zones")
    assert "do **not** open by asking the user for a polygon" in content
    assert "plan_frame_capture" in content
    assert "preview_zone" in content and "commit_zone" in content
    assert "nothing is persisted" in content
    assert "still nothing persisted" in content
    assert "exclude shelving" in content
    assert "get **no** zoneview" in content
    assert "a zoneview is **not** a zone" in content
    assert "subjective geometry is never persisted before approval" in content


def test_analytics_skill_publishes_the_exact_operator_table():
    content = skill("queries-dashboards-alerts")
    assert "| more than 2 / over 2 / above 2 | `>` with value 2 |" in content
    assert "| at least 2 / 2 or more | `>=` with value 2 |" in content
    assert '"More than 2" fires at 3, not at 2.' in content
    lowered = flat("queries-dashboards-alerts")
    assert "the query computes. a dashboard only presents" in lowered
    assert "`distinct` raw local tracker ids" in lowered
    assert "allow_partial" in lowered
    assert "must never infer zero because a required camera went stale" in lowered


def test_multiview_skill_forbids_counting_local_track_ids():
    content = flat("multiview-fusion")
    assert "never a count of camera bounding boxes" in content
    assert "never `distinct` raw local tracker ids across cameras" in content
    assert "they are not identity" in content
    assert "`known`" in content and "`partial`" in content and "`unknown`" in content
    assert "two cameras seeing one person is one person" in content


def test_sources_skill_keeps_the_credential_and_media_boundaries():
    content = flat("sources-and-cameras")
    assert "get_source_connection" in content
    assert "never log, print, display, persist, or echo it" in content
    assert "no api returns live camera pixels" in content
    assert "plan_frame_capture" in content
    assert "before** asking the user for coordinates" in content


def test_guided_demo_skill_never_calls_replay_live():
    content = flat("guided-demo")
    assert "never describe replay as live inference or live fusion" in content
    assert "no worker row and no heartbeat" in content
    assert "keep camera & space setup" in content
    assert "do not copy the demo's alert rule" in content


# ---------------------------------------------------------------------------
# the workflow registry agrees with the skills and tools
# ---------------------------------------------------------------------------

def test_every_workflow_points_at_skills_that_exist():
    for name, item in agent_workflows.WORKFLOWS.items():
        assert item["skills"], f"{name} must name at least one skill"
        for skill_name in item["skills"]:
            assert skill_name in EXPECTED_SKILLS, f"{name} points at unknown skill {skill_name}"
        assert item["when"] and item["sequence"] and item["invariants"]
        assert item["done_when"]


def test_every_workflow_tool_is_a_curated_public_tool():
    from test_mcp_server import CURATED_PUBLIC_TOOLS
    for name, item in agent_workflows.WORKFLOWS.items():
        for tool in item["tools"]:
            assert tool in CURATED_PUBLIC_TOOLS, \
                f"workflow {name} names {tool}, which is not advertised"


def test_the_agent_manual_routes_without_duplicating_the_skills():
    content = read(os.path.join(ROOT, "docs", "agents", "AGENTS.md"))
    lowered = content.lower()
    assert "inspect_workspace()" in content
    assert "list_workflows()" in content and "get_workflow(name)" in content
    assert "get_skill(name)" in content
    for name in EXPECTED_SKILLS:
        assert f"skills/{name}/SKILL.md" in content, f"{name} must be discoverable from AGENTS.md"
    for retired in RETIRED_SKILLS - EXPECTED_SKILLS:
        assert f"skills/{retired}/" not in content, \
            f"AGENTS.md still links retired {retired}"
    assert "observe locally, derive centrally" in lowered
    assert "do not treat an arbitrary repository script" in lowered
    # AGENTS.md routes; it must not become a second copy of every playbook.
    assert len(content.split()) < 1500, "AGENTS.md should point at skills, not replace them"


def test_the_agent_surface_document_lists_the_real_tool_set():
    from test_mcp_server import CURATED_PUBLIC_TOOLS
    content = read(os.path.join(ROOT, "docs", "agent-surface.md"))
    for tool in CURATED_PUBLIC_TOOLS:
        assert f"`{tool}`" in content, f"docs/agent-surface.md omits {tool}"
    assert "19 tools" in content
    assert "MANYSIGHT_MCP_LEGACY_TOOLS=1" in content
    assert "59" in content, "the legacy count must be stated"
    assert "no language model runs" in content.lower()


def test_claude_md_points_at_the_current_skill_names():
    content = read(os.path.join(ROOT, "docs", "agents", "CLAUDE.md"))
    assert "skills/manysight-core/SKILL.md" in content
    assert "manysight-platform" not in content
    assert "docs/agent-surface.md" in content


# ---------------------------------------------------------------------------
# relative links
# ---------------------------------------------------------------------------

LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


@pytest.mark.parametrize("path", sorted(MARKDOWN_FILES))
def test_relative_markdown_links_resolve(path):
    broken = []
    for target in LINK.findall(read(path)):
        target = target.split()[0]
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        resolved = os.path.normpath(os.path.join(os.path.dirname(path), target.split("#")[0]))
        if not os.path.exists(resolved):
            broken.append(target)
    assert broken == [], f"{os.path.relpath(path, ROOT)} has broken links: {broken}"
