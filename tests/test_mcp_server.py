"""mcp_server/server.py against MCP Python SDK v2 (mcp>=2.0.0, MCPServer). This
suite locks in that the StoreLens tool contract survived the v1->v2 SDK
migration (mcp_server/_transport.py is the compatibility boundary) and that the
transport dispatch (stdio vs streamable-http, host/port/security config) is
correct without ever starting a real transport (which would block on stdin or
bind a socket)."""
import asyncio

import pytest


def test_server_module_imports_without_crashing():
    """The actual regression: v1's `from mcp.server.fastmcp import FastMCP` no
    longer resolves against an installed mcp>=2.0.0."""
    import mcp_server.server  # noqa: F401


# The advertised surface is a product decision, not an accident: an agent that
# has to choose between sixty tools rediscovers the architecture by trial and
# error. Changing this list means changing the curated interface on purpose.
CURATED_PUBLIC_TOOLS = {
    # context and workflows
    "inspect_workspace", "list_workflows", "get_workflow", "get_skill",
    # sources and cameras
    "inspect_source", "configure_source", "get_source_connection", "plan_frame_capture",
    # geometry and zones
    "preview_zone", "commit_zone",
    # perception
    "inspect_perception", "get_worker_recipe", "request_worker_state",
    # multiview
    "configure_multiview_group",
    # analytics
    "run_query", "configure_saved_query", "configure_dashboard", "configure_alert",
    # destructive workspace operation
    "reset_cameras",
}


def test_curated_public_tool_surface_is_exactly_as_designed():
    import mcp_server.server as m

    tools = asyncio.run(m.mcp.list_tools())
    names = {t.name for t in tools}
    assert names == CURATED_PUBLIC_TOOLS
    assert len(tools) == 19
    assert set(m.PUBLIC_TOOLS) == CURATED_PUBLIC_TOOLS, \
        "PUBLIC_TOOLS documents the advertised surface and must match it"


def test_low_level_tools_are_demoted_but_not_deleted():
    """Legacy handlers stay implemented and importable; they are just not
    advertised, so REST/SDK parity survives without cluttering the agent's
    choice. Nothing that supersedes them may itself be demoted."""
    import mcp_server.server as m

    tools = asyncio.run(m.mcp.list_tools())
    advertised = {t.name for t in tools}
    legacy_names = {fn.__name__ for fn in m.LEGACY_TOOLS}
    assert not legacy_names & advertised
    assert len(m.LEGACY_TOOLS) == 59
    for superseded in ("list_sources", "get_store_map", "create_zone", "create_zone_view",
                       "extend_zone_from_view", "submit_observations", "query_data",
                       "create_saved_query", "add_dashboard_widget", "create_alert_rule",
                       "get_observation_contract", "list_skills", "register_worker",
                       "heartbeat_worker", "list_current_fused_entities", "submit_events"):
        assert callable(getattr(m, superseded)), f"{superseded} must remain callable"
        assert superseded in legacy_names
    # Retired adapters remain reachable in code but are never advertised.
    assert {"register_insight", "list_insights", "delete_insight"} & set(dir(m))
    assert not {"register_insight", "list_insights", "delete_insight"} & advertised


def test_legacy_compatibility_mode_re_advertises_the_low_level_tools(monkeypatch):
    """A deployment that still drives the old tool names has a migration path."""
    import importlib
    import mcp_server.server as m

    monkeypatch.setenv("STORELENS_MCP_LEGACY_TOOLS", "1")
    legacy = importlib.reload(m)
    try:
        names = {t.name for t in asyncio.run(legacy.mcp.list_tools())}
        assert CURATED_PUBLIC_TOOLS <= names
        assert "list_sources" in names and "query_data" in names
        assert len(names) == 19 + 59
    finally:
        monkeypatch.delenv("STORELENS_MCP_LEGACY_TOOLS", raising=False)
        importlib.reload(m)


def test_tool_descriptions_tell_an_agent_when_to_use_them():
    import mcp_server.server as m

    # Descriptions are wrapped docstrings; collapse whitespace before matching.
    tools = {t.name: " ".join((t.description or "").split()).lower()
             for t in asyncio.run(m.mcp.list_tools())}
    for name, description in tools.items():
        assert len(description) > 120, f"{name} needs an operationally useful description"
        assert any(phrase in description
                   for phrase in ("use it", "use them", "call it", "first call")), \
            f"{name}'s description must say when to use it"
    assert "without persisting" in tools["preview_zone"]
    assert "approved=true" in tools["commit_zone"]
    assert "never normalized" in tools["configure_alert"]
    assert "'more than 2' -> operator='>'" in tools["configure_alert"]
    assert "before starting any worker" in tools["inspect_perception"]
    assert "do not infer the contract from an example script" in tools["get_worker_recipe"]
    assert "never image bytes" in tools["plan_frame_capture"]
    assert "first call" in tools["inspect_workspace"]


def test_server_instructions_state_the_invariants_up_front():
    import mcp_server.server as m

    instructions = m.mcp.instructions.lower()
    assert "inspect_workspace() first" in instructions
    assert "observe locally, derive centrally" in instructions
    assert "detections=[] is an explicit known zero" in instructions
    assert "never fake a detection" in instructions
    assert "'more than 2' is > 2 and 'at least 2' is >= 2" in instructions
    assert "never infer it from an example, demo, or older worker script" in instructions
    assert "opaque source-local tracker id" in instructions
    assert "one canonical zone, never one" in instructions
    # The stale legacy marker must not be taught as the current path any more.
    assert "detection_frame_count" not in instructions


def test_read_only_tool_invocation(monkeypatch):
    """A representative tool call through the real MCP call_tool machinery,
    not just a direct Python call -- exercises argument validation + dispatch
    under the new SDK."""
    import mcp_server.server as m

    monkeypatch.setattr(m, "get_platform_config", lambda: {
        key: f"http://test/{key}" for key in (
            "dashboard_url", "rest_url", "openapi_url", "docs_url", "mcp_url", "agent_guide_url")})
    result = asyncio.run(m.mcp.call_tool("get_skill", {"name": "storelens-core"}))
    assert result.is_error is False
    skill = result.structured_content["result"]
    assert "observe locally, derive centrally" in skill.lower()
    assert "http://test/rest_url" in skill, "the resolved runtime endpoints are prefixed"


def test_an_unknown_skill_names_the_available_ones():
    import mcp_server.server as m

    with pytest.raises(Exception) as excinfo:
        m.get_skill("storelens-platform")   # the pre-consolidation name
    assert "storelens-core" in str(excinfo.value), \
        "a stale skill name must self-correct in one turn"


def test_configure_alert_refuses_an_operator_it_was_not_given(monkeypatch):
    """The operator is the user's word, so an unknown one is an error, never a
    substitution."""
    import mcp_server.server as m

    monkeypatch.setattr(m, "_req", lambda *args, **kwargs: {})
    for bad in ("greater", "more than", "=>", ""):
        with pytest.raises(ValueError, match="operator must be one of"):
            m.configure_alert("rule", query_id=1, operator=bad, value=2)
    with pytest.raises(ValueError, match="query_condition needs"):
        m.configure_alert("rule", operator=">", value=2)


def test_curated_tools_route_to_the_agent_endpoints(monkeypatch):
    import mcp_server.server as m

    calls = []
    monkeypatch.setattr(m, "_req",
                        lambda method, path, body=None, **kwargs: calls.append((method, path)) or {})
    m.inspect_workspace()
    m.inspect_source(4)
    m.plan_frame_capture(4)
    m.inspect_perception(source_ids=[3, 4])
    m.get_worker_recipe(source_ids=[3, 4])
    m.list_workflows()
    m.get_workflow("define-zone-from-cameras")
    m.preview_zone(views=[{"source_id": 3, "polygon_px": []}])
    m.commit_zone(views=[], approved=True, zone_name="Aisle 04")
    assert calls == [
        ("GET", "/agent/workspace?entity_type=person"),
        ("GET", "/agent/sources/4?entity_type=person"),
        ("GET", "/agent/sources/4/frame-capture-plan"),
        ("GET", "/agent/perception?entity_type=person&source_ids=3%2C4"
                "&require_tracking=true&require_spatial=true"),
        ("GET", "/agent/worker-recipe?entity_type=person&tracking=true&source_ids=3%2C4"),
        ("GET", "/agent/workflows"),
        ("GET", "/agent/workflows/define-zone-from-cameras"),
        ("POST", "/agent/zone-preview"),
        ("POST", "/agent/zone-commit"),
    ]


def test_invalid_tool_name_raises():
    import mcp_server.server as m
    from mcp_server._transport import ToolError

    with pytest.raises(ToolError):
        asyncio.run(m.mcp.call_tool("not_a_real_tool", {}))


def test_missing_required_argument_raises():
    import mcp_server.server as m
    from mcp_server._transport import ToolError

    with pytest.raises(ToolError):
        asyncio.run(m.mcp.call_tool("get_skill", {}))  # name: str is required


def test_streamable_http_app_is_constructable():
    """Confirms the HTTP/streamable transport actually wires up under the
    installed SDK version, beyond just checking our own dispatch kwargs."""
    import mcp_server.server as m
    from starlette.applications import Starlette

    app = m.mcp.streamable_http_app()
    assert isinstance(app, Starlette)


class _RecordingServer:
    """Stands in for a real MCPServer so run_server()'s dispatch can be
    checked without starting a real transport (stdio would block on stdin;
    streamable-http would bind a socket)."""

    def __init__(self):
        self.calls = []

    def run(self, **kwargs):
        self.calls.append(kwargs)


def test_stdio_transport_receives_no_http_config():
    from mcp_server._transport import run_server

    stub = _RecordingServer()
    run_server(stub, "stdio", host="127.0.0.1", port=8001,
               dns_rebinding_protection=True, allowed_hosts=["x"], allowed_origins=["y"])
    assert stub.calls == [{"transport": "stdio"}]


def test_streamable_http_transport_receives_host_port_and_security():
    from mcp_server._transport import run_server

    stub = _RecordingServer()
    run_server(stub, "streamable-http", host="0.0.0.0", port=9000,
               dns_rebinding_protection=False, allowed_hosts=["a"], allowed_origins=["b"])
    assert len(stub.calls) == 1
    call = stub.calls[0]
    assert call["transport"] == "streamable-http"
    assert call["host"] == "0.0.0.0"
    assert call["port"] == 9000
    assert call["stateless_http"] is True
    security = call["transport_security"]
    assert security.enable_dns_rebinding_protection is False
    assert security.allowed_hosts == ["a"]
    assert security.allowed_origins == ["b"]


def test_build_server_accepts_no_transport_config():
    """The v2-critical part of the constructor migration: build_server() must
    not pass host/port/stateless_http/transport_security to MCPServer.__init__
    -- v2 dropped them from the constructor entirely."""
    from mcp_server._transport import build_server

    server = build_server("test-server", instructions="hello")
    assert server.name == "test-server"
    assert server.instructions == "hello"


def test_managed_connection_tool_uses_privileged_request(monkeypatch):
    import mcp_server.server as m

    calls = []
    monkeypatch.setattr(m, "_req", lambda method, path, **kwargs: calls.append((method, path, kwargs)) or {})
    m.get_source_connection(9)
    assert calls == [("GET", "/sources/9/connection", {"privileged": True})]
