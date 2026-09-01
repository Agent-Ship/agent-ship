"""A broken tool degrades into an answer, instead of taking the turn down.

The tools phase claims that a tool which raises comes back to the model as something it can
read and recover from — so one broken integration does not lose the user's turn. This is the
demo slice for that claim, and it covers both tool sources, because they take different code
paths: a native tool is wrapped when it is built, an MCP tool is wrapped after it is
discovered. The MCP half was crashing turns until recently while the docs said otherwise.

Keyless: the tools are invoked directly, so the assertion is about the tool boundary and needs
no model.
"""

from __future__ import annotations

import pytest
from agentship.tools import Tool
from agentship_langgraph.tools import survive_tool_errors, to_langchain_tool
from langchain_core.tools import StructuredTool
from pydantic import BaseModel


class _Args(BaseModel):
    """One argument, so a model could call these tools."""

    ref: str


def _refuse(ref: str) -> str:
    """Stand in for an integration that is down or rejects the request."""
    raise RuntimeError(f"upstream refused {ref}")


async def test_a_broken_native_tool_answers_instead_of_raising():
    """A native tool that raises returns a readable failure the model can act on."""
    tool = to_langchain_tool(Tool("charge", "charge a card", _refuse, args_schema=_Args))

    answer = await tool.ainvoke({"ref": "inv-42"})

    assert isinstance(answer, str), "a failing tool must answer, not raise"
    assert "charge" in answer and "upstream refused inv-42" in answer, (
        f"the model cannot tell what failed from: {answer!r}"
    )


async def test_a_broken_mcp_tool_answers_the_same_way():
    """An MCP tool gets the identical treatment — the two are meant to be indistinguishable.

    MCP tools arrive already built from the MCP client, so they miss the wrapping a native
    tool gets at construction. Until that was fixed, one failing MCP server crashed the turn.
    """
    discovered = StructuredTool.from_function(
        coroutine=_refuse, name="mcp_charge", description="charge a card", args_schema=_Args
    )

    answer = await survive_tool_errors(discovered).ainvoke({"ref": "inv-42"})

    assert "mcp_charge" in answer and "upstream refused inv-42" in answer


async def test_an_unwrapped_tool_would_have_crashed_the_turn():
    """Pins what the wrapping prevents: the raw tool propagates its exception."""
    raw = StructuredTool.from_function(
        coroutine=_refuse, name="mcp_charge", description="charge a card", args_schema=_Args
    )
    with pytest.raises(RuntimeError):
        await raw.ainvoke({"ref": "inv-42"})
