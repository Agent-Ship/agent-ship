"""A tiny local MCP server bundled with the demo (Phase 03 — MCP).

Exposes one tool, ``days_between``, over the **stdio** transport (the local-server transport). The
demo agent (`agents/mcp/agent.yaml`) connects to this as an MCP server and the model calls the tool.
Requires the MCP extra: `pip install "agentship-langgraph[mcp]"`. Run standalone with
`python agents/mcp/time_server.py`.
"""

from __future__ import annotations

from datetime import date

from mcp.server.fastmcp import FastMCP

server = FastMCP("time-tools")


@server.tool()
def days_between(start: str, end: str) -> int:
    """Return the number of days between two ISO dates (YYYY-MM-DD), end minus start."""
    return (date.fromisoformat(end) - date.fromisoformat(start)).days


if __name__ == "__main__":
    server.run()  # stdio transport by default
