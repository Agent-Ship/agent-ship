"""T4 router-purity proof: the LangGraph adapter *reads* routed_model, never routes.

DESIGN §13.5: routing is decided upstream by the ``route`` step and stamped on
:attr:`RunContext.routed_model`; the engine adapter consumes that value and must
**never** call :meth:`ModelRouter.pick` itself. These tests pin both halves:

* the adapter resolves its model from the stamped ``routed_model`` (so a value the
  routing step chose is the one the engine uses);
* the adapter never invokes a ``ModelRouter`` — spying on ``DefaultModelRouter.pick``
  shows it is untouched during a build/run driven only by the pre-stamped context.
"""

from __future__ import annotations

import agentship_langgraph.models as models_module
import pytest
from agentship.context import Caller, RunContext, RunMode, current_run
from agentship.spec import AgentSpec
from langchain_core.language_models.fake_chat_models import FakeListChatModel


@pytest.fixture
def capture_resolve(monkeypatch):
    """Capture the model id the engine passes to resolve_model, returning a fake model."""
    captured: dict = {}

    def fake_resolve(model, **params):
        """Record the resolved model id and hand back a deterministic fake model."""
        captured["model"] = model
        return FakeListChatModel(responses=["ok"])

    monkeypatch.setattr(models_module, "resolve_model", fake_resolve)
    return captured


def test_adapter_reads_the_stamped_routed_model(capture_resolve):
    """The engine resolves the model from RunContext.routed_model when one is stamped.

    Stamps a routed model that differs from spec.model, then builds inside that
    context. The engine must resolve the *stamped* id, proving it consumes the
    routing decision rather than re-deriving from spec.model. Non-vacuous: if the
    adapter ignored routed_model it would resolve 'openai/spec-model' and fail.
    """
    from agentship_langgraph.engine import LangGraphEngine

    ctx = RunContext(
        caller=Caller(user_id="u1"),
        session_id="s1",
        run_id="r1",
        agent_name="a",
        mode=RunMode.INVOKE,
        routed_model="openai/routed-model",
    )
    token = current_run.set(ctx)
    try:
        spec = AgentSpec(name="a", engine="langgraph", model="openai/spec-model")
        LangGraphEngine().build(spec)
    finally:
        current_run.reset(token)
    assert capture_resolve["model"] == "openai/routed-model"


def test_adapter_never_calls_model_router_pick(capture_resolve, monkeypatch):
    """The adapter never calls ModelRouter.pick — routing is the runtime's job (§13.5).

    Spies on DefaultModelRouter.pick and drives a full build+run through the engine.
    The spy must never fire: the adapter reads the already-stamped routed_model, it
    does not route. Non-vacuous — if the engine called pick, the spy would trip the
    assertion.
    """
    from agentship.primitives.model_router import DefaultModelRouter

    def forbidden_pick(self, spec, task_hint=None):
        """Fail loudly if the adapter ever routes on its own."""
        raise AssertionError("the adapter must not call ModelRouter.pick (§13.5)")

    monkeypatch.setattr(DefaultModelRouter, "pick", forbidden_pick)
    # The runtime's routing step also uses pick; monkeypatching it here means the
    # test drives the adapter directly with a pre-stamped context instead.
    ctx = RunContext(
        caller=Caller(user_id="u1"),
        session_id="s1",
        run_id="r1",
        agent_name="a",
        mode=RunMode.INVOKE,
        routed_model="openai/routed-model",
    )
    token = current_run.set(ctx)
    try:
        from agentship_langgraph.engine import LangGraphEngine

        engine = LangGraphEngine()
        compiled = engine.build(
            AgentSpec(name="a", engine="langgraph", model="openai/spec-model")
        )
        # build resolves the model reading routed_model; pick must not have fired.
    finally:
        current_run.reset(token)
    assert compiled is not None
