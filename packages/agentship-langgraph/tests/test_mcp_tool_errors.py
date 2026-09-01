"""An MCP tool that raises must degrade like a native one, not crash the turn.

Native tools go through ``to_langchain_tool``, which turns a raise into an error message
the model can read and recover from. MCP tools were appended to the bound list straight
from the MCP client, so they never passed through that guard: one failing MCP server took
down the whole turn while the capability page claimed a failing tool degrades. Both kinds
are meant to be indistinguishable to the agent — including when they fail.

Offline: a stub tool stands in for a discovered MCP tool, so no server and no key.
"""

from __future__ import annotations

import pytest
from agentship_langgraph.tools import survive_tool_errors
from langchain_core.tools import StructuredTool
from pydantic import BaseModel


class _LookupArgs(BaseModel):
    """Arguments of the stub MCP tool — one term to look up."""

    term: str


async def _always_fails(term: str) -> str:
    """Stand in for an MCP server that is down or rejects the call."""
    raise RuntimeError(f"mcp server refused {term}")


def _failing_mcp_tool() -> StructuredTool:
    """A LangChain tool shaped exactly like one returned by the MCP client."""
    return StructuredTool.from_function(
        coroutine=_always_fails,
        name="mcp_lookup",
        description="look something up",
        args_schema=_LookupArgs,
    )


async def test_a_failing_mcp_tool_returns_an_error_the_model_can_read():
    """The guarded tool answers with the failure instead of raising it."""
    guarded = survive_tool_errors(_failing_mcp_tool())

    answer = await guarded.ainvoke({"term": "widgets"})

    assert isinstance(answer, str), "a failing MCP tool must answer, not raise"
    assert "mcp_lookup" in answer and "mcp server refused widgets" in answer, (
        f"the model cannot tell what failed from: {answer!r}"
    )


async def test_an_unguarded_mcp_tool_would_have_crashed_the_turn():
    """Pins the bug this guard fixes: without it the raise escapes into the graph."""
    with pytest.raises(RuntimeError):
        await _failing_mcp_tool().ainvoke({"term": "widgets"})


async def test_the_guard_passes_a_working_tool_straight_through():
    """A tool that succeeds is unchanged — the guard only intercepts failures."""

    async def _works(term: str) -> str:
        """Return a normal result."""
        return f"found {term}"

    ok = StructuredTool.from_function(
        coroutine=_works,
        name="mcp_lookup",
        description="look something up",
        args_schema=_LookupArgs,
    )
    assert await survive_tool_errors(ok).ainvoke({"term": "widgets"}) == "found widgets"


async def test_the_guard_keeps_the_tools_identity():
    """Name, description and args schema survive wrapping, so the model still sees one tool."""
    original = _failing_mcp_tool()
    guarded = survive_tool_errors(original)

    assert guarded.name == original.name
    assert guarded.description == original.description
    assert guarded.args_schema == original.args_schema


async def test_the_engine_actually_wraps_the_tools_it_discovers(monkeypatch):
    """The guard is APPLIED, not merely available.

    The previous tests prove ``survive_tool_errors`` works in isolation; this one proves the
    engine puts discovered MCP tools through it. Without this, the guard could exist and the
    engine could still bind raw MCP tools — which is exactly the bug being fixed.
    """
    import agentship_langgraph.mcp as mcp_module
    from agentship.spec import AgentSpec
    from agentship_langgraph.engine import LangGraphEngine

    monkeypatch.setattr(mcp_module, "discover_mcp_tools_sync", lambda _cfg: [_failing_mcp_tool()])

    spec = AgentSpec(
        name="a",
        engine="langgraph",
        template="single",
        model="x",
        mcp={"stub": {"transport": "stdio", "command": "true", "args": []}},
    )
    bound = LangGraphEngine()._resolve_tools(spec)

    assert [t.name for t in bound] == ["mcp_lookup"], "the MCP tool was not bound"
    # The decisive assertion: invoking the BOUND tool answers instead of raising.
    answer = await bound[0].ainvoke({"term": "widgets"})
    assert "mcp server refused widgets" in answer
