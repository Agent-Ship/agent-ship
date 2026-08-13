"""C7.1: ``AgentRef`` — an in-process handle to another agent, invoked via its ``run`` port.

A supervisor dispatches to a specialist by name: ``AgentRef.resolve(name, registry)`` looks it up
(unregistered → fail-fast ``CapabilityError``) and ``ref.run(message, ctx)`` calls the specialist's
``run`` — the *same* public port any caller uses, so a specialist on any engine works transparently
— then wraps the output as a :class:`SpecialistResult`. Output is normalized to a plain dict whether
the specialist returns a Pydantic model, a dict, or a scalar.
"""

from __future__ import annotations

import pytest
from agentship.context import Caller, RunContext, RunMode
from agentship.engines.base import Result
from agentship.errors import CapabilityError
from agentship.primitives.dispatch import AgentRef
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
