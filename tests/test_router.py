"""Live slice: the `ModelRouter` — the router picks the model, then a real turn runs.

Routing is a pure, deterministic, request-time choice of which model id a turn uses
(DESIGN §13.5): the runtime's ``route`` step runs before the engine and stamps the
chosen id on :attr:`RunContext.routed_model`; the engine reads that value and never
routes itself. The default router (``DefaultModelRouter``) is a simple pass-through
today — it returns exactly the model the spec declares.

This slice makes the routing real end to end: it asks the router which model to use,
then runs one real ``gpt-4o-mini`` turn through the framework and asserts a real,
non-empty answer comes back. There is no fake engine and no recording — this makes a
live call to OpenAI.

Run it (with a key set):

    set -a; source ../agentship/.env; set +a
    pytest tests/test_router.py -q

Without a key the live test skips cleanly.
"""

from __future__ import annotations

import pytest

from agentship import build_agent
from agentship.primitives.model_router import (
    DefaultModelRouter,
    resolve_model_router,
)
from agentship.spec import AgentSpec

ROUTED_MODEL = "openai/gpt-4o-mini"


def test_default_router_picks_spec_model_deterministically():
    """The default (pass-through) router returns spec.model, the same id every time."""
    router = DefaultModelRouter()
    spec = AgentSpec(name="a", engine="langgraph", model=ROUTED_MODEL)

    picks = {router.pick(spec) for _ in range(50)}

    assert picks == {ROUTED_MODEL}
    assert isinstance(resolve_model_router(), DefaultModelRouter)


@pytest.mark.vcr
async def test_router_picks_then_a_real_turn_runs_through_it():
    """The router picks the model id, then a real turn through that model answers live.

    First the pass-through router chooses the model id from the spec; then the same
    spec runs a real turn against that model and returns a real, non-empty answer —
    the picked id and the live answer together, no fakes.
    """
    spec = AgentSpec(
        name="router-demo",
        engine="langgraph",
        template="single",
        model=ROUTED_MODEL,
        prompt="You are a concise assistant. Answer in one short sentence.",
    )

    picked = DefaultModelRouter().pick(spec)
    assert picked == ROUTED_MODEL

    agent = build_agent(spec)
    result = await agent.run("Name one primary color.")

    assert isinstance(result.output, str)
    assert result.output.strip() != ""
