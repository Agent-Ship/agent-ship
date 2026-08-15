"""Convert a vendor-neutral :class:`agentship.tools.Tool` to a LangChain tool (Phase 03 · C1).

The core ``Tool`` is engine-agnostic; the LangGraph engine binds tools as LangChain tools
objects (what ``create_react_agent`` expects). This adapter is the one place that knows both types,
so vendor imports stay confined to ``agentship-langgraph`` and the core never sees LangChain.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agentship.context import get_run_context
from agentship.primitives.idempotency import DictLedger, call_once, idem_key
from langchain_core.tools import StructuredTool

if TYPE_CHECKING:
    from agentship.tools import Tool

#: The write-ahead intent ledger for side-effecting tool calls (Phase 03 · C4). A process-shared
#: in-memory ledger — like the InMemorySaver singleton, it lets an in-process run→resume see the
#: recorded "done" entry so a resumed run never re-fires a tool's side effect. A durable
#: Postgres-backed ledger under the same `IdempotencyLedger` protocol lands in P11 (durable tasks).
_TOOL_LEDGER = DictLedger()


class _ToolInvocation:
    """A ``call_once`` ``fn``: runs the tool, and on the pending path declines to re-fire."""

    def __init__(self, tool: Tool, kwargs: dict[str, Any]) -> None:
        """Bind the tool and its validated call arguments."""
        self._tool = tool
        self._kwargs = kwargs

    async def __call__(self) -> str:
        """Execute the tool's side effect (the fresh-call path)."""
        return await self._tool.run(**self._kwargs)

    async def verify(self) -> str:
        """Pending path (crash mid-write): we can't introspect a generic tool, so do NOT re-fire.

        A tool that can check whether its effect landed can supply a richer verify later; the safe
        default is to not repeat a write whose outcome is unknown.
        """
        return f"(idempotency) a prior '{self._tool.name}' call may have completed; not re-run"


def to_langchain_tool(tool: Tool, *, confirm_writes: bool = False) -> StructuredTool:
    """Wrap a core :class:`~agentship.tools.Tool` as a LangChain ``StructuredTool``.

    The wrapper exposes the tool's ``name``/``description``/``args_schema`` (so the model sees a
    typed tool-call) and routes execution to the core tool's async ``run`` — the single source of
    truth for what the tool does, whether it is a built-in tool, an authored function, or an MCP
    tool. Two behaviours attach to a ``side_effecting`` tool:

    - **HITL confirm** (``confirm_writes``, Phase 03 · C5): before the effect runs, the wrapper
      ``interrupt()``\\s with the pending write so a human approves it; a non-``approved`` decision
      returns a "rejected" message and the effect never fires.
    - **Exactly-once** (Phase 03 · C4): execution goes through
      :func:`~agentship.primitives.idempotency.call_once` keyed by ``idem_key(thread_id, "tool",
      name, args)`` so a resumed run replays the recorded result instead of re-firing.
    """

    async def _run(**kwargs: object) -> str:
        """Async coroutine LangChain invokes — delegates to the core tool's ``run``."""
        if not tool.side_effecting:
            return await tool.run(**kwargs)
        if confirm_writes:
            from langgraph.types import interrupt

            decision = interrupt({"action": "confirm_write", "tool": tool.name, "args": kwargs})
            if not (isinstance(decision, dict) and decision.get("approved")):
                return f"the write to {tool.name!r} was rejected by the human and was not executed"
        thread_id = get_run_context().session_id
        key = idem_key(thread_id, "tool", tool.name, kwargs)
        return await call_once(_TOOL_LEDGER, key, _ToolInvocation(tool, dict(kwargs)),
                               idempotent=False)

    return StructuredTool.from_function(
        coroutine=_run,
        name=tool.name,
        description=tool.description,
        args_schema=tool.args_schema,
    )
