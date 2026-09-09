"""A Python MCP server must run under the interpreter that spawned the agent."""

from __future__ import annotations

import sys

from agentship.spec import McpServerSpec
from agentship_langgraph.mcp import to_connections


def test_a_bare_python_command_uses_the_running_interpreter() -> None:
    """``command: python3`` means this Python, not whatever PATH resolves to.

    Outside an activated virtualenv, PATH's ``python3`` is the system interpreter, which does
    not have ``mcp`` installed. The server subprocess then dies on import and the agent sees
    only "Connection closed" — a message that points at the server rather than at the
    interpreter that could not import its dependency. Worse, the same spec passes or fails
    depending on shell state.
    """
    servers = {
        "time": McpServerSpec(transport="stdio", command="python3", args=["server.py"]),
    }
    assert to_connections(servers)["time"]["command"] == sys.executable


def test_an_explicit_command_is_left_exactly_as_written() -> None:
    """An absolute path or a non-Python command is someone being specific on purpose."""
    servers = {
        "node": McpServerSpec(transport="stdio", command="npx", args=["-y", "some-server"]),
        "pinned": McpServerSpec(transport="stdio", command="/usr/bin/python3.11", args=["s.py"]),
    }
    connections = to_connections(servers)
    assert connections["node"]["command"] == "npx"
    assert connections["pinned"]["command"] == "/usr/bin/python3.11"
