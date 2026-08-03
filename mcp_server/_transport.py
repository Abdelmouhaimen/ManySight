"""Compatibility boundary for the MCP Python SDK's server/transport API.

server.py's ~600 lines of tool definitions are business logic; this module is
the only place that knows which SDK generation is installed, so an upstream
SDK change touches this file and nothing else.

SDK v1 -> v2 (mcp>=2.0.0) changed two things this codebase depended on:
  - `FastMCP` was renamed `MCPServer` and moved from `mcp.server.fastmcp` to
    `mcp.server.mcpserver`; `TransportSecuritySettings` moved from
    `mcp.server.fastmcp.server` to `mcp.server.transport_security`.
  - `host`/`port`/`stateless_http`/`transport_security` moved off the server
    constructor entirely onto `run()` (and the `*_app()` builders) -- v2 draws
    a line between what the server *is* (name, instructions, tools) and how it
    is *served* (transport, bind address, security). The three
    TransportSecuritySettings fields themselves (`enable_dns_rebinding_protection`,
    `allowed_hosts`, `allowed_origins`) are unchanged.

The decorator surface (`@mcp.tool()` and friends) is unchanged between v1 and
v2, so no other file needs to change.
"""
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings

__all__ = ["MCPServer", "ToolError", "TransportSecuritySettings", "build_server", "run_server"]


def build_server(name: str, instructions: str) -> MCPServer:
    """Construct the server. Transport/bind/security config is intentionally
    NOT accepted here -- see run_server()."""
    return MCPServer(name, instructions=instructions)


def run_server(mcp: MCPServer, transport: str, host: str, port: int,
               dns_rebinding_protection: bool, allowed_hosts: list[str],
               allowed_origins: list[str]) -> None:
    """Run `mcp` on stdio or streamable-http. stdio takes no transport-specific
    configuration; host/port/security apply only to streamable-http."""
    if transport == "stdio":
        mcp.run(transport="stdio")
        return
    mcp.run(
        transport=transport, host=host, port=port, stateless_http=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=dns_rebinding_protection,
            allowed_hosts=allowed_hosts,
            allowed_origins=allowed_origins,
        ),
    )
