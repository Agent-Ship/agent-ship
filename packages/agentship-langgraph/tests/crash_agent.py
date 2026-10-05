"""A durable refund agent run as a SEPARATE PROCESS, so a test can ``kill -9`` it mid-turn.

Not collected by pytest (no ``test_`` prefix); ``test_crash_recovery_postgres.py`` runs it with
``python crash_agent.py`` and drives it entirely through the environment:

- ``CRASH_PHASE``: ``first`` runs the turn; ``resume`` resumes it by session, with NO token —
  the crashed process never returned one.
- ``CRASH_MODE``: where the first phase hangs, waiting to be killed:
  ``mid_effect`` — inside the refund, after it happened but before it returned (ledger: pending);
  ``after_effect`` — after the refund returned and the ledger said ``done``, but before its task
  finished, so LangGraph saved nothing for it (the hang is in ``on_tool_end``, which LangChain
  runs inside the tool's task);
  ``parallel`` — in a second, read-only tool call running beside a refund that has finished.
- ``CRASH_DIR``: where the refund appends one line per firing (``effects``) and the hang point
  drops a ``ready`` marker for the test to wait on.
- ``CRASH_ORDER`` / ``CRASH_TENANT`` / ``CRASH_SESSION``: unique per test.

On success it prints one JSON line: ``{"output": ...}``.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from agentship.tools import Tool
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import BaseModel

_PHASE = os.environ.get("CRASH_PHASE", "first")
_MODE = os.environ.get("CRASH_MODE", "mid_effect")
_DIR = Path(os.environ.get("CRASH_DIR", "."))
_EFFECTS = _DIR / "effects"
_READY = _DIR / "ready"


async def _hang_here() -> None:
    """Tell the test we are at the kill point, then wait to be killed.

    Async, because the core ``Tool`` calls a plain function inline on the event loop: a blocking
    sleep here would freeze the parallel refund this is supposed to wait behind.
    """
    _READY.write_text("ready")
    await asyncio.sleep(300)


class _RefundArgs(BaseModel):
    order_id: str


async def _refund(order_id: str) -> str:
    """The side effect: one durable line per firing — the thing that must happen exactly once."""
    with _EFFECTS.open("a") as f:
        f.write(order_id + "\n")
        f.flush()
        os.fsync(f.fileno())
    firings = len(_EFFECTS.read_text().splitlines())
    if _PHASE == "first" and _MODE == "mid_effect":
        await _hang_here()
    return f"refund #{firings} issued for {order_id}"


class _NoArgs(BaseModel):
    pass


async def _stall() -> str:
    """Read-only; in ``after_effect`` it holds the tool step open once the refund has fired."""
    if _PHASE == "first" and _MODE == "parallel":
        while not _EFFECTS.exists():
            await asyncio.sleep(0.02)
        await _hang_here()
    return "stall finished"


refund = Tool("refund", "refunds an order", _refund, args_schema=_RefundArgs, side_effecting=True)
stall = Tool("stall", "waits", _stall, args_schema=_NoArgs)


class _CrashModel(BaseChatModel):
    """Asks for the tool calls on a fresh turn; answers with the tool results once they are in.

    Decided by the history, not by a call counter, because the resuming process has a new model
    object that has never been called — it must still answer correctly from the checkpoint.
    """

    order: str
    mode: str
    model: str = "openai/gpt-4o-mini"

    @property
    def _llm_type(self) -> str:
        return "crash-script"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        if isinstance(messages[-1], ToolMessage):
            results = [m.content for m in messages if isinstance(m, ToolMessage)]
            reply = AIMessage(content="final: " + " | ".join(results))
        else:
            calls = [{"name": "refund", "args": {"order_id": self.order}, "id": "call_refund"}]
            if self.mode == "parallel":
                calls.append({"name": "stall", "args": {}, "id": "call_stall"})
            reply = AIMessage(content="", tool_calls=calls)
        return ChatResult(generations=[ChatGeneration(message=reply)])


async def _main() -> None:
    import agentship_langgraph.models as models
    from agentship.context import Caller
    from agentship.runtime import build_agent
    from agentship.spec import AgentSpec

    order = os.environ["CRASH_ORDER"]
    fake = _CrashModel(order=order, mode=_MODE)
    models.resolve_model = lambda *a, **k: fake
    if _PHASE == "first" and _MODE == "after_effect":
        from agentship_langgraph.tools import ToolCallLogger

        async def _hang_after_refund(self, output, **kwargs) -> None:
            if "refund" in str(getattr(output, "content", output)):
                await _hang_here()

        ToolCallLogger.on_tool_end = _hang_after_refund
    agent = build_agent(
        AgentSpec(
            name="payments",
            engine="langgraph",
            template="single",
            model="x",
            prompt="Refund what is asked.",
            tools=["crash_agent:refund", "crash_agent:stall"],
            durability="checkpoint",
        )
    )
    caller = Caller(user_id="u", tenant_id=os.environ["CRASH_TENANT"])
    session = os.environ["CRASH_SESSION"]
    if _PHASE == "first":
        result = await agent.run(f"refund order {order}", caller=caller, session_id=session)
    else:
        result = await agent.resume(caller=caller, session_id=session)
    print(json.dumps({"output": result.output}), flush=True)


if __name__ == "__main__":
    asyncio.run(_main())
