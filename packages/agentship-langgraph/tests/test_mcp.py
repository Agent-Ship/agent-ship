"""Phase 03 · C3 — MCP tools via ``langchain-mcp-adapters`` (local stdio server).

Two levels: an offline unit test of the config translation (no subprocess), and an integration test
that spawns the ``mcp_echo_server.py`` fixture as a **local stdio** MCP server, discovers its
``shout`` tool through the engine, and calls it end-to-end — no model, no network. This proves the
OSS client stack is wired correctly without us hand-rolling the MCP protocol.
"""

from __future__ import annotations

import sys
from pathlib import Path

import agentship_langgraph.models as models_module
from agentship.context import Caller, RunContext, RunMode
from agentship.spec import AgentSpec, McpServerSpec
from agentship_langgraph.engine import LangGraphEngine
from agentship_langgraph.mcp import mcp_version_ok, to_connections
from langchain_core.messages import AIMessage
from test_tool_execution import ScriptedModel


def test_mcp_version_guard_accepts_the_installed_pin():
    """With the pinned mcp SDK installed, the version guard reports OK (>=1.28,<2)."""
    ok, installed = mcp_version_ok()
    assert ok is True
    assert installed is not None and installed.startswith("1.")


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


async def test_one_agent_uses_a_native_tool_and_an_mcp_tool_in_the_same_turn(monkeypatch):
    """One agent calls the built-in ``calculator`` **and** the MCP ``shout`` tool in a single turn.

    This is the headline claim of the phase: once bound, an MCP tool and a native tool are
    indistinguishable to the agent. The scripted model emits both tool calls in *one* assistant
    message, so the ReAct loop runs them side by side in the same turn, and both results come back
    before the model's answer. Offline and keyless — the MCP tool is served by a real stdio
    subprocess, the model is a fake.
    """
    spec = AgentSpec(
        name="a",
        engine="langgraph",
        template="single",
        model="x",
        tools=["calculator"],
        mcp={"echo": McpServerSpec(transport="stdio", command=sys.executable, args=[_FIXTURE])},
    )
    calls_both = AIMessage(
        content="",
        tool_calls=[
            {"name": "calculator", "args": {"expression": "2 + 2"}, "id": "c1"},
            {"name": "shout", "args": {"text": "done"}, "id": "c2"},
        ],
    )
    model = ScriptedModel(script=[calls_both, AIMessage(content="2 + 2 is 4, and DONE.")])
    monkeypatch.setattr(models_module, "resolve_model", lambda *a, **k: model)

    engine = LangGraphEngine()
    compiled = engine.build(spec)
    assert sorted(compiled.bound_tools) == ["calculator", "shout"]

    ctx = RunContext(
        caller=Caller(user_id="u"),
        session_id="native-and-mcp",
        run_id="r",
        agent_name="a",
        mode=RunMode.INVOKE,
    )
    result = await engine.run(compiled, "add 2 and 2, then shout 'done'", ctx)

    # The second prompt is what the model saw after the tool node ran — one turn, both results.
    returned = {m.name: str(m.content) for m in model.seen[1] if m.type == "tool"}
    assert "4" in returned["calculator"], returned
    assert "DONE" in returned["shout"], returned
    assert result.output == "2 + 2 is 4, and DONE."
