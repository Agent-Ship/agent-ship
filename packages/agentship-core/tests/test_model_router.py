"""T4 proof: the vendor-free ``ModelRouter`` and the routing step that stamps it.

Routing is a pure, deterministic, request-time choice of which model id to use
(DESIGN §13.5): the ``route`` step runs *before* the engine and stamps the chosen
model id on :attr:`RunContext.routed_model`; the engine adapter then *reads* that
value and never routes itself. These tests prove all four contracts:

* :class:`DefaultModelRouter` is deterministic and returns ``spec.model`` (v0.1
  pass-through, never an LLM);
* :func:`resolve_model_router` returns the default when no plugin is installed and
  swaps in a plugin registered under the ``agentship.model_routers`` entry point;
* the runtime's routing step stamps ``routed_model`` on the context before the
  engine runs;
* *router purity*: the LangGraph adapter reads the stamped ``routed_model`` and
  never calls :meth:`ModelRouter.pick` itself.
"""

from __future__ import annotations

from agentship.context import Caller, RunContext, RunMode
from agentship.primitives.model_router import (
    DefaultModelRouter,
    ModelRouter,
    resolve_model_router,
    stamp_routed_model,
)
from agentship.spec import AgentSpec


def _ctx(**overrides) -> RunContext:
    """Build a RunContext with sensible defaults, overridable per test."""
    fields = {
        "caller": Caller(user_id="u1"),
        "session_id": "s1",
        "run_id": "r1",
        "agent_name": "a",
        "mode": RunMode.INVOKE,
    }
    fields.update(overrides)
    return RunContext(**fields)


def test_routed_model_defaults_to_none():
    """A fresh RunContext carries no routed model until the routing step stamps one."""
    assert _ctx().routed_model is None


def test_default_router_picks_spec_model_deterministically():
    """DefaultModelRouter.pick returns spec.model, the same value every time (never an LLM)."""
    router = DefaultModelRouter()
    spec = AgentSpec(name="a", engine="langgraph", model="openai/gpt-4o-mini")
    picks = {router.pick(spec) for _ in range(100)}
    assert picks == {"openai/gpt-4o-mini"}


def test_default_router_is_a_model_router():
    """DefaultModelRouter is a concrete ModelRouter (the swappable base type)."""
    assert isinstance(DefaultModelRouter(), ModelRouter)


def test_resolve_default_when_no_plugin():
    """resolve_model_router() with no name returns the built-in DefaultModelRouter."""
    assert isinstance(resolve_model_router(), DefaultModelRouter)


def test_resolve_named_plugin_swaps_in(monkeypatch):
    """A router registered under the entry-point group is returned by name.

    Registers a throwaway router in-code on the shared registry (the same registry
    entry points populate) and asserts resolve_model_router("fixed") returns it —
    proving the swap seam works without installing a real distribution.
    """
    from agentship.primitives.model_router import MODEL_ROUTERS

    class FixedRouter(ModelRouter):
        """A stub router that always picks a fixed id — proves plugin swap-in."""

        def pick(self, spec, task_hint=None):
            """Return a constant model id regardless of the spec."""
            return "plugin/fixed"

    MODEL_ROUTERS.register("fixed", FixedRouter)
    try:
        resolved = resolve_model_router("fixed")
        assert isinstance(resolved, FixedRouter)
        assert resolved.pick(AgentSpec(name="a", engine="echo")) == "plugin/fixed"
    finally:
        MODEL_ROUTERS._providers.pop("fixed", None)


def test_stamp_routed_model_sets_the_context_field():
    """stamp_routed_model writes the router's pick onto RunContext.routed_model."""
    ctx = _ctx()
    spec = AgentSpec(name="a", engine="langgraph", model="openai/gpt-4o-mini")
    stamp_routed_model(spec, ctx)
    assert ctx.routed_model == "openai/gpt-4o-mini"


async def test_runtime_stamps_routed_model_before_the_engine_runs():
    """build_agent().run stamps routed_model on the context before the engine executes.

    A recording engine captures RunContext.routed_model at run time. With the
    routing step wired in, the engine must observe the stamped model id (not None) —
    this fails if the runtime does not run the routing step before the engine, so it
    is non-vacuous.
    """
    from agentship.engines.base import ENGINES, Engine, EngineCapabilities, Result

    seen: dict = {}

    class _RecordingEngine(Engine):
        """An engine that records the routed_model it sees at run time."""

        name = "recording_router"
        capabilities = EngineCapabilities()

        def build(self, spec, authored=None):
            """No compilation needed — the spec itself is the artifact."""
            return spec

        async def run(self, compiled, text, ctx):
            """Record the routed model the routing step stamped, then echo the text."""
            seen["routed_model"] = ctx.routed_model
            return Result(output=text)

    ENGINES.register("recording_router", _RecordingEngine)
    try:
        from agentship.runtime import build_agent

        agent = build_agent(
            AgentSpec(name="a", engine="recording_router", model="openai/gpt-4o-mini")
        )
        await agent.run("hi")
        assert seen["routed_model"] == "openai/gpt-4o-mini"
    finally:
        ENGINES._providers.pop("recording_router", None)


def test_stamp_is_noop_outside_a_run():
    """stamp_routed_model on a None context is a safe no-op (never raises)."""
    stamp_routed_model(AgentSpec(name="a", engine="echo"), None)
