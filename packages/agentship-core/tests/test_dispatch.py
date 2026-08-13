"""C7.1: ``AgentRef`` — an in-process handle to another agent, invoked via its ``run`` port.

A supervisor dispatches to a specialist by name: ``AgentRef.resolve(name, registry)`` looks it up
(unregistered → fail-fast ``CapabilityError``) and ``ref.run(message, ctx)`` calls the specialist's
``run`` — the *same* public port any caller uses, so a specialist on any engine works transparently
— then wraps the output as a :class:`SpecialistResult`. Output is normalized to a plain dict whether
the specialist returns a Pydantic model, a dict, or a scalar.
"""

from __future__ import annotations

import asyncio

import pytest
from agentship.context import Caller, RunContext, RunMode
from agentship.engines.base import Result
from agentship.errors import CapabilityError
from agentship.primitives.dispatch import AgentRef, dispatch
from pydantic import BaseModel


class _FakeAgent:
    """A stand-in specialist: records what it was asked and returns a fixed output."""

    def __init__(self, output: object) -> None:
        self._output = output
        self.seen: tuple | None = None

    async def run(self, text: str, *, user_id: str = "anonymous") -> Result:
        self.seen = (text, user_id)
        return Result(output=self._output)


def _registry(**agents: object) -> dict:
    """A minimal name→agent registry (any object with ``get`` satisfies AgentRef)."""
    return dict(agents)


def _ctx() -> RunContext:
    return RunContext(
        caller=Caller(user_id="alice"),
        session_id="s1",
        run_id="r1",
        agent_name="supervisor",
        mode=RunMode.INVOKE,
    )


def test_resolve_unregistered_name_fails_fast():
    """Dispatching to an unknown agent raises CapabilityError, not a None deref later."""
    with pytest.raises(CapabilityError):
        AgentRef.resolve("ghost", _registry())


def test_resolve_returns_a_handle_to_the_agent():
    """A registered name resolves to an AgentRef bound to that agent."""
    agent = _FakeAgent("hi")
    ref = AgentRef.resolve("flights", _registry(flights=agent))
    assert ref.name == "flights" and ref.agent is agent


async def test_run_wraps_output_as_specialist_result():
    """ref.run returns a SpecialistResult naming the specialist, error None on success."""
    ref = AgentRef.resolve("flights", _registry(flights=_FakeAgent({"price": 200})))
    out = await ref.run("cheapest flight?", _ctx())
    assert out["name"] == "flights"
    assert out["output"] == {"price": 200}
    assert out["error"] is None


async def test_run_calls_the_specialist_under_the_callers_user():
    """The specialist runs under its OWN turn, carrying the caller's user_id."""
    agent = _FakeAgent("done")
    ref = AgentRef.resolve("x", _registry(x=agent))
    await ref.run("go", _ctx())
    assert agent.seen == ("go", "alice")


async def test_run_normalizes_a_pydantic_output():
    """A Pydantic-model output is model_dump()'d into the SpecialistResult's dict."""

    class Answer(BaseModel):
        price: int
        confidence: float

    ref = AgentRef.resolve("h", _registry(h=_FakeAgent(Answer(price=9, confidence=0.8))))
    out = await ref.run("q", _ctx())
    assert out["output"] == {"price": 9, "confidence": 0.8}
    assert out["confidence"] == 0.8  # lifted from the output for the resolver's tie-break


async def test_run_normalizes_a_scalar_output():
    """A plain string/scalar output is wrapped as {'output': value} (still a dict)."""
    ref = AgentRef.resolve("h", _registry(h=_FakeAgent("just text")))
    out = await ref.run("q", _ctx())
    assert out["output"] == {"output": "just text"}
    assert out["confidence"] is None


class _BoomAgent:
    """A specialist that always raises — proving a failure becomes an error result, not a crash."""

    async def run(self, text: str, *, user_id: str = "anonymous") -> Result:
        raise RuntimeError("specialist exploded")


def _ref(name: str, agent: object) -> AgentRef:
    """Build an AgentRef directly around a fake agent (skips registry lookup)."""
    return AgentRef(name, agent)


async def test_single_strategy_runs_one_specialist():
    """`single` dispatches to exactly the one ref and returns its result in a list."""
    ref = _ref("a", _FakeAgent({"v": 1}))
    results = await dispatch("single", [ref], "go", _ctx())
    assert [r["name"] for r in results] == ["a"]
    assert results[0]["output"] == {"v": 1}


async def test_parallel_strategy_fans_out_to_all():
    """`parallel` runs every ref with the same message and preserves order."""
    refs = [_ref("a", _FakeAgent({"v": 1})), _ref("b", _FakeAgent({"v": 2}))]
    results = await dispatch("parallel", refs, "go", _ctx())
    assert [r["name"] for r in results] == ["a", "b"]
    assert [r["output"]["v"] for r in results] == [1, 2]


async def test_sequential_strategy_threads_output_into_the_next():
    """`sequential` feeds each specialist's output text as the next specialist's input."""
    second = _FakeAgent({"v": 2})
    refs = [_ref("a", _FakeAgent("step-1-out")), _ref("b", second)]
    await dispatch("sequential", refs, "start", _ctx())
    assert second.seen == ("step-1-out", "alice")  # b received a's output, not "start"


async def test_a_failing_specialist_becomes_an_error_result_not_a_crash():
    """An exception in one specialist yields a SpecialistResult with `error` set; others run."""
    refs = [_ref("ok", _FakeAgent({"v": 1})), _ref("bad", _BoomAgent())]
    results = await dispatch("parallel", refs, "go", _ctx())
    by_name = {r["name"]: r for r in results}
    assert by_name["ok"]["error"] is None
    assert by_name["bad"]["error"] and "exploded" in by_name["bad"]["error"]


async def test_unknown_strategy_fails_fast():
    """An unknown dispatch strategy raises CapabilityError rather than silently doing nothing."""
    with pytest.raises(CapabilityError):
        await dispatch("teleport", [_ref("a", _FakeAgent("x"))], "go", _ctx())


class _SlowAgent:
    """A specialist that sleeps before answering — to exercise the per-node timeout."""

    def __init__(self, delay: float) -> None:
        self._delay = delay

    async def run(self, text: str, *, user_id: str = "anonymous") -> Result:
        await asyncio.sleep(self._delay)
        return Result(output={"v": "slow"})


async def test_timeout_turns_a_slow_specialist_into_an_error_result():
    """A specialist exceeding timeout_s becomes an error result; the fast one still succeeds."""
    refs = [_ref("fast", _FakeAgent({"v": 1})), _ref("slow", _SlowAgent(1.0))]
    results = await dispatch("parallel", refs, "go", _ctx(), timeout_s=0.05)
    by_name = {r["name"]: r for r in results}
    assert by_name["fast"]["error"] is None and by_name["fast"]["output"] == {"v": 1}
    assert by_name["slow"]["error"] and "timed out" in by_name["slow"]["error"]


async def test_no_timeout_lets_a_slow_specialist_finish():
    """With no timeout (default), a slow specialist completes normally."""
    results = await dispatch("single", [_ref("slow", _SlowAgent(0.01))], "go", _ctx())
    assert results[0]["error"] is None and results[0]["output"] == {"v": "slow"}
