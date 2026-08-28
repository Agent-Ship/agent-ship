"""Phase 02 conformance cells: durability + single-owner + resume guarantees (§11).

Named cells that pin the phase's core promises against the LangGraph engine:

* ``durable_resume_after_kill`` — a brand-new engine (standing in for a process restart) resumes a
  durable run from its checkpoint to a byte-identical result.
* ``resume_token_roundtrip`` — a token reconstructed from ``(engine, blob)`` alone (no field
  inspection, the way P09 will re-hydrate it from JSONB) still drives a successful resume.
* ``two_workers_one_thread`` — two concurrent acquires of one thread's lock: exactly one proceeds,
  the other gets ``ThreadBusyError``.
* ``reclaim_mid_flight`` — a worker dies holding a thread mid-run; a second worker is refused
  while the dead hold stands, then reclaims the thread and completes the run.
* ``regression_single_agent`` — a plain non-durable agent is unchanged (mints no token).

``replay_idempotency`` now lands here (unblocked by P03 tool execution): a side-effecting tool is
wrapped in ``call_once``, so a resumed invocation with the same args replays the recorded result
instead of re-firing the effect. ``hitl_reject`` is proven in the engine suite
(``test_langgraph_hitl.py``).
"""

from __future__ import annotations

import asyncio

import agentship_langgraph.models as models_module
import pytest
from agentship.context import Caller, RunContext, RunMode
from agentship.engines.base import ResumeToken
from agentship.errors import ThreadBusyError
from agentship.runtime import build_agent
from agentship.spec import AgentSpec
from agentship.thread_lock import InMemoryThreadLock
from agentship_langgraph.engine import LangGraphEngine
from langchain_core.language_models.fake_chat_models import FakeListChatModel


@pytest.fixture
def fake_model(monkeypatch):
    """A deterministic model + the in-memory saver path (no DB) for every cell."""
    monkeypatch.delenv("AGENT_SESSION_STORE_URI", raising=False)
    fake = FakeListChatModel(responses=["primary colors: red, green, blue"])
    monkeypatch.setattr(models_module, "resolve_model", lambda *a, **k: fake)
    return fake


def _ctx(session_id: str) -> RunContext:
    return RunContext(
        caller=Caller(user_id="u1"),
        session_id=session_id,
        run_id="r1",
        agent_name="a",
        mode=RunMode.INVOKE,
    )


def _durable_spec() -> AgentSpec:
    return AgentSpec(name="a", engine="langgraph", model="x", prompt="p", durability="checkpoint")


async def test_cell_durable_resume_after_kill(fake_model):
    """A fresh engine (a process restart) resumes the durable run to the identical output."""
    spec = _durable_spec()
    ctx = _ctx("cell-kill")
    first = await LangGraphEngine().run(LangGraphEngine().build(spec), "hi", ctx)
    # "kill -9": a brand-new engine + rebuild resumes from the shared durable checkpoint.
    resumed = await LangGraphEngine().resume(LangGraphEngine().build(spec), first.resume_token, ctx)
    assert resumed.output == first.output


async def test_cell_resume_token_roundtrip(fake_model):
    """A token rebuilt from (engine, blob) alone — no field inspection — still resumes."""
    spec = _durable_spec()
    ctx = _ctx("cell-roundtrip")
    engine = LangGraphEngine()
    compiled = engine.build(spec)
    first = await engine.run(compiled, "hi", ctx)
    rebuilt = ResumeToken(engine=first.resume_token.engine, blob=dict(first.resume_token.blob))
    resumed = await engine.resume(compiled, rebuilt, ctx)
    assert resumed.output == first.output


async def test_cell_two_workers_one_thread():
    """Two concurrent acquires of one thread's lock: exactly one proceeds, the other is refused."""
    outcomes: list[str] = []
    started = asyncio.Event()

    async def worker(hold: float) -> None:
        try:
            async with InMemoryThreadLock("t", "shared-thread"):
                outcomes.append("held")
                started.set()
                await asyncio.sleep(hold)
        except ThreadBusyError:
            outcomes.append("busy")

    first = asyncio.create_task(worker(0.05))
    await started.wait()
    await asyncio.gather(first, asyncio.create_task(worker(0.0)))
    assert sorted(outcomes) == ["busy", "held"]


async def test_cell_replay_idempotency():
    """A side-effecting tool fires exactly once across a replay (the P02-deferred cell, now landed).

    Mirrors "counting side-effect tool → kill after tool → resume": the tool wrapper routes a
    side-effecting call through ``call_once`` keyed by the canonical ``idem_key``, so re-invoking it
    with the same args (what a resumed run does) reads the recorded result and never re-fires.
    """
    from agentship.context import current_run
    from agentship.tools import Tool
    from agentship_langgraph.tools import to_langchain_tool
    from pydantic import BaseModel

    class _Args(BaseModel):
        label: str

    fired: list[str] = []
    tool = to_langchain_tool(
        Tool("charge", "charges once", lambda label: fired.append(label) or "charged",
             args_schema=_Args, side_effecting=True)
    )
    token = current_run.set(_ctx("cell-idem"))
    try:
        first = await tool.ainvoke({"label": "order-1"})
        replay = await tool.ainvoke({"label": "order-1"})  # the resume re-fire
    finally:
        current_run.reset(token)

    assert len(fired) == 1, "side effect fired more than once across the replay"
    assert first == replay


async def test_cell_reclaim_mid_flight(fake_model):
    """Worker A dies holding a thread mid-run; worker B reclaims the thread and finishes the run.

    A takes the thread's lock and runs until the HITL pause — a genuine mid-flight state: a
    checkpoint is written, the run is not finished. A is then killed ``-9``: its ``__aexit__``
    never runs, so the hold outlives the worker. While that dead hold stands B is refused
    (``ThreadBusyError``), which is what keeps a second writer off the thread. Once the
    environment reclaims the dead owner's hold — Postgres does this by itself when the owner's
    session dies; the in-process lock needs ``release_dead_owner`` — B acquires the thread and
    drives the paused run to completion from A's checkpoint.
    """
    hitl_spec = AgentSpec(
        name="reclaim-cell", engine="langgraph", code="agentship_langgraph.testing:build_hitl_agent"
    )
    thread = "cell-reclaim"

    worker_a_lock = InMemoryThreadLock("t", thread)
    await worker_a_lock.__aenter__()
    worker_a = build_agent(hitl_spec)
    paused = await worker_a.engine.run(worker_a.compiled, "send it", _ctx(thread))
    assert paused.output is None and paused.resume_token is not None, "A never reached mid-flight"

    del worker_a_lock  # "kill -9": no unlock, no __aexit__ — the hold survives the worker

    resumed_while_a_held: list[str] = []
    with pytest.raises(ThreadBusyError):
        async with InMemoryThreadLock("t", thread):
            resumed_while_a_held.append("resumed")  # pragma: no cover - must not be reached
    assert resumed_while_a_held == [], "B drove the thread while the dead owner's hold stood"

    InMemoryThreadLock.release_dead_owner("t", thread)

    async with InMemoryThreadLock("t", thread):
        worker_b = build_agent(hitl_spec)
        done = await worker_b.engine.resume(
            worker_b.compiled, paused.resume_token, _ctx(thread), resume_value={"approved": True}
        )
    assert done.output == "email sent", "B did not complete the run A left mid-flight"


async def test_cell_regression_single_agent(fake_model):
    """A plain non-durable single agent is unchanged: it answers and mints no resume token."""
    agent = build_agent(AgentSpec(name="plain", engine="langgraph", model="x", prompt="p"))
    result = await agent.run("hi")
    assert result.output == "primary colors: red, green, blue"
    assert result.resume_token is None
