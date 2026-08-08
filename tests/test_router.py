"""Keyless slice: the `ModelRouter` mechanism — route step stamps, adapter reads.

Routing is a pure, deterministic, request-time choice of which model id a turn
uses (DESIGN §13.5): the runtime's ``route`` step runs *before* the engine and
stamps the chosen id on :attr:`RunContext.routed_model`; the engine adapter then
*reads* that value and never routes itself.

This slice is a real mechanism demo, not a stub and not an LLM call. It proves,
with no API key and no network:

* :class:`DefaultModelRouter` deterministically picks ``spec.model``;
* the runtime stamps that pick onto the context *before the engine runs* — proven
  by a recording engine that captures ``ctx.routed_model`` at run time.
"""

from __future__ import annotations

from agentship import build_agent
from agentship.context import Caller, RunContext, RunMode
from agentship.engines.base import ENGINES, Engine, EngineCapabilities, Result
from agentship.primitives.model_router import (
    DefaultModelRouter,
    resolve_model_router,
    stamp_routed_model,
)
from agentship.spec import AgentSpec

ROUTED_MODEL = "openai/gpt-4o-mini"


def test_default_router_picks_spec_model_deterministically():
    """DefaultModelRouter.pick returns spec.model, the same id every time (never an LLM)."""
    router = DefaultModelRouter()
    spec = AgentSpec(name="a", engine="langgraph", model=ROUTED_MODEL)

    picks = {router.pick(spec) for _ in range(50)}

    assert picks == {ROUTED_MODEL}
    assert isinstance(resolve_model_router(), DefaultModelRouter)


def test_stamp_writes_the_routed_model_onto_the_context():
    """stamp_routed_model writes the router's pick onto RunContext.routed_model."""
    ctx = RunContext(
        caller=Caller(user_id="u1"),
        session_id="s1",
        run_id="r1",
        agent_name="a",
        mode=RunMode.INVOKE,
    )
    assert ctx.routed_model is None  # unset until the route step runs

    stamp_routed_model(AgentSpec(name="a", engine="langgraph", model=ROUTED_MODEL), ctx)

    assert ctx.routed_model == ROUTED_MODEL


async def test_runtime_stamps_routed_model_before_the_engine_and_adapter_reads_it():
    """A real run stamps routed_model before the engine, and the engine (adapter) reads it.

    A recording engine captures ``ctx.routed_model`` at run time. Because the
    runtime runs the ``route`` step before the engine, the engine observes the
    stamped id — not ``None``. This is the exact seam a real adapter uses to resolve
    its model, exercised keyless.
    """
    seen: dict = {}

    class _RecordingEngine(Engine):
        """An engine that records the routed_model it sees at run time, then echoes."""

        name = "demo_recording_router"
        capabilities = EngineCapabilities()

        def build(self, spec, authored=None):
            """No compilation needed — the spec itself is the artifact."""
            return spec

        async def run(self, compiled, text, ctx):
            """Record the routed model the route step stamped, then echo the text."""
            seen["routed_model"] = ctx.routed_model
            return Result(output=text)

    ENGINES.register("demo_recording_router", _RecordingEngine)
    try:
        agent = build_agent(
            AgentSpec(name="a", engine="demo_recording_router", model=ROUTED_MODEL)
        )
        await agent.run("hi")
        assert seen["routed_model"] == ROUTED_MODEL
    finally:
        ENGINES._providers.pop("demo_recording_router", None)
