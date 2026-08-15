"""Phase 03 · C3 — MCP tools via ``langchain-mcp-adapters`` (local stdio server).

Two levels: an offline unit test of the config translation (no subprocess), and an integration test
that spawns the ``mcp_echo_server.py`` fixture as a **local stdio** MCP server, discovers its
``shout`` tool through the engine, and calls it end-to-end — no model, no network. This proves the
OSS client stack is wired correctly without us hand-rolling the MCP protocol.
"""

from __future__ import annotations

import sys
from pathlib import Path

from agentship.spec import AgentSpec, McpServerSpec
from agentship_langgraph.engine import LangGraphEngine
from agentship_langgraph.mcp import to_connections

_FIXTURE = str(Path(__file__).parent / "fixtures" / "mcp_echo_server.py")


def test_to_connections_translates_both_transports():
    """The ``mcp:`` block becomes a MultiServerMCPClient connections dict for both transports."""
    mcp = {
        "files": McpServerSpec(transport="stdio", command="npx", args=["-y", "srv"]),
        "gh": McpServerSpec(
            transport="streamable_http", url="https://h/mcp", headers={"Authorization": "Bearer T"}
        ),
    }
    conns = to_connections(mcp)
    assert conns["files"] == {"transport": "stdio", "command": "npx", "args": ["-y", "srv"]}
    assert conns["gh"]["transport"] == "streamable_http"
    assert conns["gh"]["url"] == "https://h/mcp"
    assert conns["gh"]["headers"] == {"Authorization": "Bearer T"}


async def test_stdio_mcp_tool_is_discovered_and_called():
    """A local stdio MCP server's tool is discovered, bound, and runs end-to-end (offline)."""
    spec = AgentSpec(
        name="a",
        engine="langgraph",
        model="x",
        mcp={"echo": McpServerSpec(transport="stdio", command=sys.executable, args=[_FIXTURE])},
    )
    tools = LangGraphEngine()._resolve_tools(spec)
    shout = next((t for t in tools if t.name == "shout"), None)
    assert shout is not None, f"MCP tool 'shout' not discovered; got {[t.name for t in tools]}"
    result = await shout.ainvoke({"text": "hello"})
    assert "HELLO" in str(result)
