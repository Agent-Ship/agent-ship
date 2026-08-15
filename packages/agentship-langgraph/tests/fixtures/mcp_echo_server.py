"""A tiny local MCP server used as a test fixture (Phase 03 · C3).

Exposes one tool, ``shout``, over the **stdio** transport (the local-server transport). The MCP
integration test spawns this as a subprocess and asserts the tool is discovered + callable through
an agent — no network, no model. Run standalone with ``python mcp_echo_server.py``.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

server = FastMCP("echo")


@server.tool()
def shout(text: str) -> str:
    """Return ``text`` uppercased — a trivial, deterministic tool to prove MCP discovery + call."""
    return text.upper()


if __name__ == "__main__":
    server.run()  # stdio transport by default
