"""Phase 03 · C1 — the LangGraph engine resolves ``spec.tools`` into executable tools.

Offline unit coverage for the wiring: a spec's ``tools:`` refs resolve to LangChain tools the
``single`` template binds into its ReAct loop, each converted from a core ``Tool``.
End-to-end *tool calling by a real model* is proven by a live demo slice (needs a key); here:
the resolution + conversion + the honest ``tool_calling`` capability flip.
"""

from __future__ import annotations

import json

from agentship.context import Caller, RunContext, RunMode, current_run
from agentship.spec import AgentSpec
from agentship.tools import Tool
from agentship_langgraph.engine import LangGraphEngine
from agentship_langgraph.tools import to_langchain_tool
from pydantic import BaseModel


class _BumpArgs(BaseModel):
    label: str


def _ctx(thread: str) -> RunContext:
    return RunContext(
        caller=Caller(user_id="u"),
        session_id=thread,
        run_id="r",
        agent_name="a",
        mode=RunMode.INVOKE,
    )


async def test_side_effecting_tool_fires_exactly_once_on_replay():
    """A side-effecting tool invoked twice with the same args (a resume re-fire) fires ONCE.

    This is the P02-deferred ``replay_idempotency`` guarantee: the engine wraps side-effecting tool
    calls in ``call_once`` keyed by ``idem_key(thread, node, tool, args)``, so a resumed run reads
    the recorded result instead of re-firing the effect.
    """
    calls = {"n": 0}

    def bump(label: str) -> str:
        calls["n"] += 1
        return f"bumped {label} (call #{calls['n']})"

    lc = to_langchain_tool(Tool("bump", "increments a counter", bump, args_schema=_BumpArgs,
                                side_effecting=True))
    token = current_run.set(_ctx("idem-thread"))
    try:
        first = await lc.ainvoke({"label": "x"})
        again = await lc.ainvoke({"label": "x"})  # the resume re-invocation
    finally:
        current_run.reset(token)

    assert calls["n"] == 1, "the side effect fired more than once across the replay"
    assert first == again  # the recorded result is replayed byte-identically


async def test_side_effecting_tool_asks_for_approval_before_firing():
    """With ``confirm_writes``, a side-effecting tool interrupts for approval; approve → fires once.

    Drives the wrapped tool from a minimal checkpointed graph (standing in for the ReAct tool node):
    the first invoke pauses (``__interrupt__`` present, effect not fired); resuming with
    ``{"approved": true}`` runs the effect exactly once.
    """
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import Command
    from typing_extensions import TypedDict

    sent: list[str] = []

    def send_email(label: str) -> str:
        sent.append(label)
        return f"sent to {label}"

    wrapped = to_langchain_tool(
        Tool("send_email", "sends email", send_email, args_schema=_BumpArgs, side_effecting=True),
        confirm_writes=True,
    )

    class _S(TypedDict, total=False):
        result: str

    async def call_tool(_state: _S) -> dict:
        return {"result": await wrapped.ainvoke({"label": "a@b.com"})}

    g = StateGraph(_S)
    g.add_node("t", call_tool)
    g.add_edge(START, "t")
    g.add_edge("t", END)
    graph = g.compile(checkpointer=InMemorySaver())
    cfg = {"configurable": {"thread_id": "hitl-approve"}}

    token = current_run.set(_ctx("hitl-approve"))
    try:
        paused = await graph.ainvoke({}, config=cfg)
        assert "__interrupt__" in paused and sent == []  # paused for approval, not fired
        done = await graph.ainvoke(Command(resume={"approved": True}), config=cfg)
    finally:
        current_run.reset(token)

    assert sent == ["a@b.com"]  # fired exactly once after approval
    assert "sent to" in done["result"]


async def test_rejected_write_is_never_executed():
    """A non-approved decision returns a rejection and the side effect never fires."""
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import Command
    from typing_extensions import TypedDict

    sent: list[str] = []
    wrapped = to_langchain_tool(
        Tool("send_email", "sends", lambda label: sent.append(label) or "ok",
             args_schema=_BumpArgs, side_effecting=True),
        confirm_writes=True,
    )

    class _S(TypedDict, total=False):
        result: str

    async def call_tool(_state: _S) -> dict:
        return {"result": await wrapped.ainvoke({"label": "x"})}

    g = StateGraph(_S)
    g.add_node("t", call_tool)
    g.add_edge(START, "t")
    g.add_edge("t", END)
    graph = g.compile(checkpointer=InMemorySaver())
    cfg = {"configurable": {"thread_id": "hitl-reject"}}

    token = current_run.set(_ctx("hitl-reject"))
    try:
        await graph.ainvoke({}, config=cfg)
        done = await graph.ainvoke(Command(resume={"approved": False}), config=cfg)
    finally:
        current_run.reset(token)

    assert sent == []  # never fired
    assert "rejected" in done["result"]


async def test_pure_tool_is_not_memoised():
    """A non-side-effecting tool is not idempotency-gated — each invocation runs (no cache)."""
    calls = {"n": 0}

    def peek(label: str) -> str:
        calls["n"] += 1
        return f"read {label}"

    lc = to_langchain_tool(Tool("peek", "reads", peek, args_schema=_BumpArgs))
    token = current_run.set(_ctx("pure-thread"))
    try:
        await lc.ainvoke({"label": "x"})
        await lc.ainvoke({"label": "x"})
    finally:
        current_run.reset(token)
    assert calls["n"] == 2


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
