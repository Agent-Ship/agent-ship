"""Convert a vendor-neutral :class:`agentship.tools.Tool` to a LangChain tool (Phase 03 · C1).

The core ``Tool`` is engine-agnostic; the LangGraph engine binds tools as LangChain tools
objects (what ``create_react_agent`` expects). This adapter is the one place that knows both types,
so vendor imports stay confined to ``agentship-langgraph`` and the core never sees LangChain.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from agentship.context import get_run_context
from agentship.primitives.idempotency import DictLedger, call_once, idem_key
from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.tools import StructuredTool

if TYPE_CHECKING:
    from agentship.tools import Tool

#: Logs every tool call (native or MCP) at INFO on ``agentship.tools`` — silent by default; enabled
#: by ``agentship run --verbose`` (and the demo). This is how you *see* that the model actually
#: invoked a tool rather than answering from its own knowledge.
_tool_logger = logging.getLogger("agentship.tools")


def _short(value: Any, limit: int = 100) -> str:
    """A one-line, length-capped preview of a value for a log line."""
    line = " ".join(str(value).split())
    return line if len(line) <= limit else f"{line[:limit]}…"


class ToolCallLogger(AsyncCallbackHandler):
    """A LangChain callback that logs each tool invocation + result on ``agentship.tools``.

    Attached to every run's config by the engine, so ``--verbose`` reveals which tool the model
    called with what arguments and what it returned — covering native tools, MCP tools, and the
    tools an autonomous agent's loop uses uniformly (they all flow through LangChain's callbacks).
    """

    async def on_tool_start(self, serialized: dict, input_str: str, **kwargs: Any) -> None:
        """Log the tool's name + input as the model invokes it."""
        name = (serialized or {}).get("name") or kwargs.get("name") or "tool"
        _tool_logger.info("call %s(%s)", name, _short(input_str))

    async def on_tool_end(self, output: Any, **kwargs: Any) -> None:
        """Log the tool's result."""
        _tool_logger.info("  = %s", _short(getattr(output, "content", output)))

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
        _tool_logger.warning("idempotency: skipping replay of %r — prior run may have completed",
                             self._tool.name)
        return f"(idempotency) a prior '{self._tool.name}' call may have completed; not re-run"


def tool_error_message(name: str, exc: Exception) -> str:
    """The text a failed tool sends back to the model — the tool's name, error type, and message.

    Kept to one plain sentence naming all three because this string *is* the model's only view of
    the failure: it has to be enough to decide whether to retry, use another tool, or apologise.
    """
    return f"tool {name!r} failed: {type(exc).__name__}: {exc}"


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

    A tool that raises returns :func:`tool_error_message` instead of propagating, so one broken
    tool degrades to something the model can read and recover from rather than crashing the turn.
    """

    async def _run(**kwargs: object) -> str:
        """Async coroutine LangChain invokes — delegates to the core tool's ``run``.

        Any failure inside the tool becomes an error *string* (the model's next prompt), never an
        exception; LangGraph's own control-flow signals (``interrupt()`` and friends, which subclass
        ``GraphBubbleUp``) are re-raised untouched so the HITL pause still works.
        """
        from langgraph.errors import GraphBubbleUp

        try:
            if not tool.side_effecting:
                return await tool.run(**kwargs)
            if confirm_writes:
                from langgraph.types import interrupt

                _tool_logger.info("HITL: pausing before write %r — awaiting approval", tool.name)
                decision = interrupt({"action": "confirm_write", "tool": tool.name, "args": kwargs})
                if not (isinstance(decision, dict) and decision.get("approved")):
                    _tool_logger.warning("HITL: write %r rejected — not executed", tool.name)
                    return (
                        f"the write to {tool.name!r} was rejected by the human "
                        f"and was not executed"
                    )
            thread_id = get_run_context().session_id
            key = idem_key(thread_id, "tool", tool.name, kwargs)
            _tool_logger.debug("running %r with idempotency guard (key=%s)", tool.name, key)
            return await call_once(_TOOL_LEDGER, key, _ToolInvocation(tool, dict(kwargs)),
                                   idempotent=False)
        except GraphBubbleUp:
            raise
        except Exception as exc:  # noqa: BLE001 — a tool is arbitrary code; any raise it makes
            # is a tool failure the model should be told about, not a crashed turn.
            _tool_logger.warning("tool %r failed: %s: %s", tool.name, type(exc).__name__, exc)
            return tool_error_message(tool.name, exc)

    return StructuredTool.from_function(
        coroutine=_run,
        name=tool.name,
        description=tool.description,
        args_schema=tool.args_schema,
    )
