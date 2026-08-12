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


def test_tool_contract_is_intact():
    import mcp_server.server as m

    tools = asyncio.run(m.mcp.list_tools())
    names = {t.name for t in tools}
    assert len(tools) == 47
    for expected in (
        "submit_observations", "get_observation_contract", "list_observations",
        "get_latest_observations", "get_latest_detection_frames", "query_analytics", "list_analysis_capabilities",
        "create_analysis", "list_analyses", "update_analysis", "delete_analysis",
        "submit_events", "get_analytics", "register_insight", "list_insights",
        "delete_insight", "list_skills", "get_skill", "register_job",
        "register_worker", "heartbeat_worker", "request_worker_state",
        "create_zone", "create_projection_surface", "create_zone_view",
        "project_points", "unproject_points",
        "get_source_connection",
    ):
        assert expected in names, f"{expected} missing from MCP tool contract"


def test_read_only_tool_invocation():
    """A representative tool call through the real MCP call_tool machinery,
    not just a direct Python call -- exercises argument validation + dispatch
    under the new SDK."""
    import mcp_server.server as m

    result = asyncio.run(m.mcp.call_tool("list_skills", {}))
    assert result.is_error is False
    names = {entry["name"] for entry in result.structured_content["result"]}
    assert "storelens-platform" in names


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
