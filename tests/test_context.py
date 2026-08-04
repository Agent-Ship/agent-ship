"""Tests for the identity backbone: RunContext, memory_scope, and contextvar isolation."""

from __future__ import annotations

import asyncio

from agentship.context import RunContext, current_run, get_run_context
from agentship.runtime import build_agent
from agentship.spec import AgentSpec


def test_memory_scope_is_user_and_agent():
    """memory_scope is (user_id, agent_name) — never keyed on session."""
    ctx = RunContext(
        user_id="u1", session_id="s1", run_id="r1", agent_name="assistant"
    )
    assert ctx.memory_scope == ("u1", "assistant")


def test_get_run_context_is_none_outside_a_run():
    """Outside any turn, the read seam returns None rather than raising."""
    assert get_run_context() is None


async def test_caller_session_id_is_honored():
    """A caller-supplied session_id is threaded onto the RunContext verbatim."""
    seen: dict[str, str] = {}

    agent = build_agent(AgentSpec(name="a", engine="echo"))

    # Reach into the context the runtime set while the engine runs.
    original_run = agent.engine.run

    async def spy(compiled, text, ctx):
        seen["session_id"] = ctx.session_id
        return await original_run(compiled, text, ctx)

    agent.engine.run = spy  # type: ignore[method-assign]
    await agent.run("hi", session_id="fixed-session")
    assert seen["session_id"] == "fixed-session"


async def test_run_id_is_unique_per_turn():
    """Each run mints a fresh run_id even when the session_id is stable across turns."""
    run_ids: list[str] = []
    session_ids: list[str] = []

    agent = build_agent(AgentSpec(name="a", engine="echo"))
    original_run = agent.engine.run

    async def spy(compiled, text, ctx):
        run_ids.append(ctx.run_id)
        session_ids.append(ctx.session_id)
        return await original_run(compiled, text, ctx)

    agent.engine.run = spy  # type: ignore[method-assign]
    await agent.run("turn one", session_id="same")
    await agent.run("turn two", session_id="same")

    assert session_ids == ["same", "same"]  # stable session
    assert run_ids[0] != run_ids[1]  # fresh per turn


async def test_concurrent_runs_do_not_bleed_context():
    """Concurrent turns each see their OWN RunContext — the contextvar never bleeds.

    Two runs are interleaved via an await point inside the engine; each asserts the
    session_id it observes is its own, proving contextvar isolation per task.
    """
    observed: dict[str, str] = {}

    agent = build_agent(AgentSpec(name="a", engine="echo"))
    original_run = agent.engine.run

    async def spy(compiled, text, ctx):
        # Yield control so the two coroutines interleave here.
        await asyncio.sleep(0)
        # The context read back must be this task's own context.
        assert current_run.get().session_id == ctx.session_id
        observed[text] = current_run.get().session_id
        await asyncio.sleep(0)
        assert current_run.get().session_id == ctx.session_id
        return await original_run(compiled, text, ctx)

    agent.engine.run = spy  # type: ignore[method-assign]

    await asyncio.gather(
        agent.run("A", session_id="session-A"),
        agent.run("B", session_id="session-B"),
    )
    assert observed == {"A": "session-A", "B": "session-B"}
