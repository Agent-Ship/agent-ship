"""Tests for the runtime: middleware onion order, on_error, and contextvar reset on all paths."""

from __future__ import annotations

import gc

import pytest
from agentship.context import current_run, get_run_context
from agentship.engines.base import Result
from agentship.middleware import Middleware
from agentship.runtime import build_agent
from agentship.spec import AgentSpec


class _Recorder(Middleware):
    """A middleware that records the order its hooks fire into a shared log."""

    def __init__(self, name: str, log: list[str]) -> None:
        """Bind this middleware's label and the shared event log."""
        self.name = name
        self.log = log

    async def on_request(self, ctx) -> None:
        """Record that on_request fired for this middleware."""
        self.log.append(f"req:{self.name}")

    async def on_response(self, ctx, result: Result) -> Result:
        """Record that on_response fired for this middleware."""
        self.log.append(f"resp:{self.name}")
        return result

    async def on_error(self, ctx, exc) -> None:
        """Record that on_error fired for this middleware."""
        self.log.append(f"err:{self.name}")


async def test_middleware_onion_order():
    """on_request runs in order; on_response runs in reverse (LIFO onion)."""
    log: list[str] = []
    agent = build_agent(
        AgentSpec(name="a", engine="echo"),
        middlewares=[_Recorder("outer", log), _Recorder("inner", log)],
    )
    await agent.run("hi")
    assert log == ["req:outer", "req:inner", "resp:inner", "resp:outer"]


async def test_on_error_fires_and_original_exception_propagates():
    """When the engine raises, on_error fires (reverse order) AND the original error propagates."""
    log: list[str] = []
    agent = build_agent(
        AgentSpec(name="a", engine="echo"),
        middlewares=[_Recorder("outer", log), _Recorder("inner", log)],
    )

    sentinel = RuntimeError("engine boom")

    async def boom(compiled, text, ctx):
        raise sentinel

    agent.engine.run = boom  # type: ignore[method-assign]

    with pytest.raises(RuntimeError) as exc:
        await agent.run("hi")
    # The ORIGINAL exception object propagates, unmasked.
    assert exc.value is sentinel
    # on_error fired for both middlewares, in reverse order.
    assert log == ["req:outer", "req:inner", "err:inner", "err:outer"]


async def test_contextvar_reset_after_run():
    """After a normal run, current_run is cleared — no leak into the caller's context."""
    agent = build_agent(AgentSpec(name="a", engine="echo"))
    await agent.run("hi")
    assert get_run_context() is None


async def test_contextvar_reset_after_error():
    """After a run that raised, current_run is still cleared."""
    agent = build_agent(AgentSpec(name="a", engine="echo"))

    async def boom(compiled, text, ctx):
        raise ValueError("x")

    agent.engine.run = boom  # type: ignore[method-assign]
    with pytest.raises(ValueError):
        await agent.run("hi")
    assert get_run_context() is None


async def test_contextvar_reset_after_early_break_of_stream():
    """Breaking out of a stream early must not leak current_run into the caller.

    This is the Phase-10b regression guard: a naive reset(token) in the generator
    finalizer would raise 'Token created in a different Context' and leave the
    caller's current_run pointing at the finished run. We break after the first
    event, force finalization, and assert the caller's context is clean.
    """
    agent = build_agent(AgentSpec(name="a", engine="echo", streaming=True))

    assert get_run_context() is None
    stream = agent.stream("hi")
    async for _event in stream:
        break  # abandon after the first event

    # Force the async generator to finalize (GeneratorExit in a foreign context).
    await stream.aclose()
    del stream
    gc.collect()

    # If the reset had crashed/leaked, this would be a stale RunContext.
    assert get_run_context() is None
    # And the contextvar itself is genuinely unset (not merely a stale object).
    assert current_run.get(None) is None
