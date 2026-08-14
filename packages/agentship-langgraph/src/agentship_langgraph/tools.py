"""Convert a vendor-neutral :class:`agentship.tools.Tool` to a LangChain tool (Phase 03 · C1).

The core ``Tool`` is engine-agnostic; the LangGraph engine binds tools as LangChain tools
objects (what ``create_react_agent`` expects). This adapter is the one place that knows both types,
so vendor imports stay confined to ``agentship-langgraph`` and the core never sees LangChain.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from langchain_core.tools import StructuredTool

if TYPE_CHECKING:
    from agentship.tools import Tool


def to_langchain_tool(tool: Tool) -> StructuredTool:
    """Wrap a core :class:`~agentship.tools.Tool` as a LangChain ``StructuredTool``.

    The wrapper exposes the tool's ``name``/``description``/``args_schema`` (so the model sees a
    typed tool-call) and routes execution to the core tool's async ``run`` — the single source of
    truth for what the tool does, whether it came from a built-in skill, an authored function, or
    (later this phase) an MCP server.
    """

    async def _run(**kwargs: object) -> str:
        """Async coroutine LangChain invokes — delegates to the core tool's ``run``."""
        return await tool.run(**kwargs)

    return StructuredTool.from_function(
        coroutine=_run,
        name=tool.name,
        description=tool.description,
        args_schema=tool.args_schema,
    )
