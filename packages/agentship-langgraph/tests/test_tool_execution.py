"""Phase 03 · C1 — the LangGraph engine resolves ``spec.tools`` into executable tools.

Offline unit coverage for the wiring: a spec's ``tools:`` refs resolve to LangChain tools the
``single`` template binds into its ReAct loop, each converted from a core ``Tool``.
End-to-end *tool calling by a real model* is proven by a live demo slice (needs a key); here:
the resolution + conversion + the honest ``tool_calling`` capability flip.
"""

from __future__ import annotations

import json

from agentship.spec import AgentSpec
from agentship_langgraph.engine import LangGraphEngine


def test_langgraph_declares_tool_calling():
    """The engine now honestly declares it can execute tools."""
    assert LangGraphEngine.capabilities.tool_calling is True


def test_resolve_tools_binds_a_builtin_skill():
    """``tools: [calculator]`` resolves to one bound LangChain tool named ``calculator``."""
    spec = AgentSpec(name="a", engine="langgraph", model="x", tools=["calculator"])
    tools = LangGraphEngine()._resolve_tools(spec)
    assert len(tools) == 1
    assert tools[0].name == "calculator"


async def test_resolved_tool_actually_runs_the_skill():
    """The converted LangChain tool executes the underlying core Tool (real calc result)."""
    spec = AgentSpec(name="a", engine="langgraph", model="x", tools=["calculator"])
    tool = LangGraphEngine()._resolve_tools(spec)[0]
    out = json.loads(await tool.ainvoke({"expression": "2 + 2 * 10"}))
    assert out["result"] == 22


def test_no_tools_resolves_to_empty_list():
    """A spec with no ``tools:`` still builds with an empty tool list (unchanged behaviour)."""
    spec = AgentSpec(name="a", engine="langgraph", model="x")
    assert LangGraphEngine()._resolve_tools(spec) == []


def test_allowed_tools_filters_the_bound_set():
    """``allowed_tools`` curates which resolved tools are exposed (the 10-MCP overload guard)."""
    keep = AgentSpec(
        name="a", engine="langgraph", model="x", tools=["calculator"], allowed_tools=["calculator"]
    )
    assert [t.name for t in LangGraphEngine()._resolve_tools(keep)] == ["calculator"]

    drop = AgentSpec(
        name="a", engine="langgraph", model="x", tools=["calculator"], allowed_tools=["other"]
    )
    assert LangGraphEngine()._resolve_tools(drop) == []
