"""Job-shaped workflow registry for coding agents.

A workflow is not a skill. A skill is the long-form playbook an agent reads; a
workflow is the short routing record that says *which* skill applies, what has
to be true first, which curated tools implement it, and what "done" means. An
agent that knows only "the user wants an occupancy alert" can find its way from
here without guessing skill filenames or rediscovering the architecture from
API trial and error.

Kept as pure data so both the REST agent surface and the tests can assert on it
without a database or a running MCP server.
"""
from __future__ import annotations

import re

# English threshold phrasing is not interchangeable, and conflating it silently
# produces an alert that is wrong by exactly one person. "More than 2" is `> 2`;
# the demo's "at least 2" is `>= 2`. ManySight never normalizes one into the
# other, so agents must map the user's own words through this table.
COMPARISON_PHRASES = {
    "more than {n}": ">",
    "over {n}": ">",
    "above {n}": ">",
    "greater than {n}": ">",
    "at least {n}": ">=",
    "{n} or more": ">=",
    "minimum of {n}": ">=",
    "fewer than {n}": "<",
    "less than {n}": "<",
    "under {n}": "<",
    "below {n}": "<",
    "at most {n}": "<=",
    "no more than {n}": "<=",
    "{n} or fewer": "<=",
    "maximum of {n}": "<=",
    "exactly {n}": "==",
    "equal to {n}": "==",
}

OPERATORS = (">", ">=", "<", "<=", "==", "!=")

_NUMBER = r"(-?\d+(?:\.\d+)?)"


def parse_threshold(phrase: str) -> dict | None:
    """Map an exact known threshold phrasing to {operator, value}.

    'more than 2' -> {'>', 2.0}; 'at least 2' -> {'>=', 2.0}. Deliberately a
    lookup over known phrasings rather than a parser: anything unrecognised
    returns None so it reaches the user as a question instead of a guess.
    """
    normalized = " ".join((phrase or "").strip().lower().split())
    for template, operator in COMPARISON_PHRASES.items():
        pattern = "^" + re.escape(template).replace(re.escape("{n}"), _NUMBER) + "$"
        match = re.match(pattern, normalized)
        if match:
            value = float(match.group(1))
            return {"operator": operator, "value": int(value) if value.is_integer() else value,
                    "phrase": normalized}
    return None


WORKFLOWS: dict[str, dict] = {
    "onboard-camera": {
        "title": "Register a camera and make it usable",
        "when": "The user wants to add a camera, stream, file, or sensor to ManySight.",
        "skills": ["sources-and-cameras"],
        "prerequisites": [
            "inspect_workspace — reuse an existing logical source instead of creating a duplicate.",
        ],
        "sequence": [
            "inspect_workspace to see whether the source already exists.",
            "configure_source with manysight_managed (structured connection plus optional "
            "credentials) or external_secret (locator.local_secret_ref only).",
            "Place the source on the map and calibrate it, or import a rich 3x4 world-to-pixel "
            "calibration, before any geometry or fusion work.",
            "inspect_source to confirm configuration, placement, and calibration readiness.",
        ],
        "invariants": [
            "Credentials never travel in locator metadata, observations, queries, dashboards, "
            "logs, or job metadata.",
            "ManySight never opens or proxies the feed; a local worker owns camera access.",
        ],
        "tools": ["inspect_workspace", "configure_source", "inspect_source"],
        "done_when": "inspect_source reports the source configured and calibrated.",
    },
    "inspect-source": {
        "title": "Understand one source before changing anything",
        "when": "You need a camera's calibration, geometry, freshness, or a look at its image.",
        "skills": ["sources-and-cameras"],
        "prerequisites": [],
        "sequence": [
            "inspect_source for configuration, placement, calibration, zone views, current "
            "sample freshness, and observed submission rate.",
            "plan_frame_capture when the task needs visual evidence, then run the returned "
            "plan in your own shell and look at the saved image.",
        ],
        "invariants": [
            "Frame capture is local to the agent or worker. ManySight is not a media proxy, so "
            "no MCP tool returns live camera pixels.",
            "get_source_connection is the only credential path and is separately authenticated.",
        ],
        "tools": ["inspect_source", "plan_frame_capture", "get_source_connection"],
        "done_when": "You can describe the source's calibration state and, if needed, its view.",
    },
    "define-zone-from-cameras": {
        "title": "Give a named physical region canonical geometry",
        "when": (
            "The user names a physical region ('Aisle 04', 'the queue') that has no canonical "
            "zone yet. Use this instead of asking the user for polygon coordinates."
        ),
        "skills": ["geometry-and-zones", "sources-and-cameras"],
        "prerequisites": [
            "inspect_workspace — confirm the zone is genuinely missing and the map exists.",
            "At least one calibrated source that can see the region.",
        ],
        "sequence": [
            "inspect_workspace to list zones and calibrated sources.",
            "Identify which calibrated cameras plausibly see the region; plan_frame_capture and "
            "look at those images. Do NOT ask the user for coordinates first.",
            "Propose image-space polygons on the floor only — exclude shelving, racks, pallets, "
            "and other objects standing on the floor.",
            "preview_zone to project them into the shared map. Nothing is persisted.",
            "Show the preview and ask the user to approve or correct it. Re-preview on every "
            "correction; subjective geometry is never persisted before approval.",
            "commit_zone once the user approves. This creates one canonical zone plus one "
            "ZoneView per contributing camera and records projection provenance.",
            "Verify the committed zone's component count, area, and provenance.",
        ],
        "invariants": [
            "A ZoneView is a camera's pixel polygon. The canonical zone is the single physical "
            "footprint in map metres. They are not the same object.",
            "Cameras that cannot see the region get no ZoneView. Never invent one to be tidy.",
            "One physical region is ONE canonical zone, never one zone per camera.",
            "Coordinates are never nudged to look neater.",
        ],
        "tools": ["inspect_workspace", "inspect_source", "plan_frame_capture", "preview_zone",
                  "commit_zone"],
        "done_when": "One canonical zone exists with a ZoneView per contributing camera.",
    },
    "run-person-tracking": {
        "title": "Get person detections flowing from real cameras",
        "when": "A question needs live or historical person positions and no healthy perception exists.",
        "skills": ["perception-workers"],
        "prerequisites": [
            "inspect_perception — reuse a healthy compatible capability instead of starting a "
            "second worker for the same sources.",
        ],
        "sequence": [
            "inspect_perception for the entity type and sources you need. If it reports "
            "action=reuse, stop: the data already exists.",
            "get_worker_recipe for the CURRENT submission contract. Never infer the contract "
            "from an example or demo script found in a repository.",
            "Inspect your own local environment (existing venv/conda env, CUDA, PyTorch, model "
            "weights) and reuse a compatible one rather than building a new environment.",
            "Write or adapt the worker against the recipe and the Python SDK, run it locally, "
            "and let it register its job/worker and heartbeat itself.",
            "Verify with inspect_perception: worker heartbeat, complete samples, freshness, and "
            "observed submission rate.",
            "Verify downstream: projection, zone membership, and fused state.",
        ],
        "invariants": [
            "One atomic DetectionSample per processed frame. detections=[] is an explicit "
            "observed zero and must be submitted, never faked as a detection.",
            "No completed fresh sample means unknown or stale, NOT zero.",
            "Workers submit detection/measurement/state only — never zones, dwell, occupancy, "
            "visits, transitions, or fused identity.",
            "Local detection and tracking may run at full camera FPS; central submission is "
            "normally a lower, task-chosen rate.",
            "entity_id is a source-local tracker ID, not an identity.",
        ],
        "tools": ["inspect_perception", "get_worker_recipe", "request_worker_state",
                  "get_source_connection"],
        "done_when": "inspect_perception reports state=healthy for every required source.",
    },
    "configure-multiview": {
        "title": "Fuse overlapping cameras into anonymous physical tracks",
        "when": "Overlapping cameras must not double-count the same person.",
        "skills": ["multiview-fusion"],
        "prerequisites": [
            "Every intended source calibrated into the same metric world/map frame.",
            "Complete samples arriving from each source.",
        ],
        "sequence": [
            "inspect_workspace for calibration status and existing groups.",
            "configure_multiview_group with the compatible sources; choose gates from "
            "calibration error, sampling rate, and walking speed.",
            "inspect_perception to confirm multiview readiness and per-source freshness.",
            "Inspect fused entities and their member evidence, not only the fused count.",
        ],
        "invariants": [
            "Fused IDs are anonymous physical-track estimates produced from geometry, time, and "
            "topology. They are not identity and not appearance ReID.",
            "Source-local tracker IDs stay local; never join them across cameras yourself.",
            "Cross-camera occupancy uses fused entities, never a count of raw local track IDs.",
            "Group quality is known, partial, or unknown; a stale required source is not zero.",
        ],
        "tools": ["inspect_workspace", "configure_multiview_group", "inspect_perception",
                  "run_query"],
        "done_when": "The group reports quality=known with member evidence from each source.",
    },
    "create-zone-occupancy-alert": {
        "title": "Alert on how many people are in a zone",
        "when": "The user wants to be told when a zone holds more/fewer than N people.",
        "skills": ["queries-dashboards-alerts", "geometry-and-zones", "multiview-fusion"],
        "prerequisites": [
            "The zone has canonical geometry — otherwise run define-zone-from-cameras first.",
            "Person perception exists — otherwise run run-person-tracking.",
            "Overlapping cameras belong to one multiview group — otherwise configure-multiview.",
        ],
        "sequence": [
            "inspect_workspace: does the zone exist, is there a multiview group, is there "
            "already an equivalent saved query?",
            "Map the user's own threshold words to an exact operator using comparison_operators "
            "below. Ask rather than guess if the phrasing is not listed.",
            "run_query to preview subject=fused_entity, measures=[current_occupancy], "
            "filters={group_ids:[g], zone_ids:[z], entity_types:['person']}.",
            "configure_saved_query once for that question, or reuse an equivalent one.",
            "configure_alert with kind=query_condition, params={query_id}, and "
            "condition={operator, value, for_seconds?, allow_partial?}.",
            "Verify the rule's stored operator and value, and the query's current value and "
            "quality.",
        ],
        "invariants": [
            "The query computes; the dashboard only presents. A widget never calculates occupancy.",
            "Current fused occupancy means fresh fused person entities inside the canonical "
            "zone — not camera bounding boxes, not DISTINCT raw local tracker IDs, not frontend "
            "polygon membership.",
            "Operators are exact. 'More than 2' is > 2 and 'at least 2' is >= 2; ManySight never "
            "converts one into the other.",
            "Quality matters: known, partial, and unknown are not the same. By default only "
            "known evidence can fire or clear an edge, so a stale camera never implies zero. "
            "Set allow_partial only when the user accepts partial coverage.",
        ],
        "tools": ["inspect_workspace", "run_query", "configure_saved_query", "configure_alert"],
        "comparison_operators": COMPARISON_PHRASES,
        "done_when": "The rule stores the exact operator and references one saved query.",
    },
    "create-generated-dashboard": {
        "title": "Show a saved question on a dashboard",
        "when": "The user asks to display, pin, or build a view from ManySight data.",
        "skills": ["queries-dashboards-alerts"],
        "prerequisites": ["A saved query that already answers the question."],
        "sequence": [
            "inspect_workspace for existing dashboards and saved queries.",
            "run_query to confirm the result shape.",
            "configure_saved_query only if no equivalent question exists.",
            "configure_dashboard with widgets whose presentation matches the shape: number for "
            "scalar, timeseries for time rows, bar for categorical, table, or heatmap.",
        ],
        "invariants": [
            "Presentation is not part of a question's identity. Changing how a saved query is "
            "displayed never justifies a second saved query.",
            "Widgets are declarative and query-backed. Agents do not generate React or SQL.",
        ],
        "tools": ["inspect_workspace", "run_query", "configure_saved_query", "configure_dashboard"],
        "done_when": "The dashboard renders the saved query's value and quality.",
    },
}

WORKFLOW_ORDER = list(WORKFLOWS)


def index() -> list[dict]:
    """The cheap routing index: enough to choose, not enough to execute."""
    return [
        {"name": name, "title": item["title"], "when": item["when"], "skills": item["skills"]}
        for name, item in WORKFLOWS.items()
    ]


def get(name: str) -> dict | None:
    item = WORKFLOWS.get(name)
    return {"name": name, **item} if item else None
