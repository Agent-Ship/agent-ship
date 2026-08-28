"""C1.2/C1.3/C1.4: the ``SupervisorAgent`` graph — pure nodes + an end-to-end routed run.

The pure nodes (lookup_route, resolve, dispatch_router) are tested in isolation with plain state
dicts; then a full supervisor is assembled and run: a fake classifier picks an intent, the request
is routed to the matching specialist(s), their answers are resolved, and the final answer comes back
through the engine. Specialists are ordinary echo agents supplied as a ``{name: agent}`` dict.
"""

from __future__ import annotations

import agentship_langgraph.models as models_module
from agentship.context import Caller, RunContext, RunMode, current_run
from agentship.runtime import build_agent
from agentship.spec import AgentSpec
from agentship_langgraph.engine import LangGraphEngine
from agentship_langgraph.templates.graph_config import GraphConfig
from agentship_langgraph.templates.graph_supervisor import (
    SupervisorAgent,
    dispatch_router,
    make_lookup_route,
    make_resolve,
)
from langchain_core.language_models.fake_chat_models import FakeListChatModel

_CFG = GraphConfig.model_validate(
    {
        "classify": {"model": "x", "intents": ["billing", "general"]},
        "routing": {
            "billing": {"specialists": ["billing_specialist"], "strategy": "single"},
            "general": {"specialists": ["faq_specialist"], "strategy": "single"},
            "_default": {"specialists": ["faq_specialist"], "strategy": "single"},
        },
        "conflict_resolver": {"priority": ["billing_specialist", "faq_specialist"]},
        "retry": {"max_attempts": 2, "on": ["specialist_error"]},
    }
)


def _result(name: str, error: str | None = None) -> dict:
    return {"name": name, "output": {"v": name}, "confidence": None, "error": error}


# ---- pure nodes -------------------------------------------------------------------------------


def test_lookup_route_maps_intent_to_specialists():
    """A known intent routes to its configured specialists + strategy."""
    out = make_lookup_route(_CFG)({"intent": "billing"})
    assert out["route"] == {"specialists": ["billing_specialist"], "strategy": "single"}


def test_lookup_route_falls_back_to_default():
    """An unknown/None intent falls back to the _default route."""
    out = make_lookup_route(_CFG)({"intent": None})
    assert out["route"]["specialists"] == ["faq_specialist"]


def test_resolve_picks_a_winner_by_priority():
    """resolve runs the ConflictResolver over the specialist results."""
    out = make_resolve(_CFG)(
        {"specialist_results": [_result("faq_specialist"), _result("billing_specialist")]}
    )
    assert out["resolved"]["winner"] == "billing_specialist"


def test_dispatch_router_retries_then_resolves():
    """The router loops while a failure is retryable under the cap, then moves on."""
    route = dispatch_router(_CFG)
    assert (
        route(
            {
                "specialist_results": [_result("billing_specialist", "boom")],
                "attempts": {"billing_specialist": 1},
            }
        )
        == "retry"
    )
    assert (
        route(
            {
                "specialist_results": [_result("billing_specialist", "boom")],
                "attempts": {"billing_specialist": 2},
            }
        )
        == "resolve"
    )
    assert (
        route({"specialist_results": [_result("billing_specialist")], "attempts": {}}) == "resolve"
    )


# ---- end-to-end -------------------------------------------------------------------------------


def _ctx() -> RunContext:
    return RunContext(
        caller=Caller(user_id="alice"),
        session_id="s1",
        run_id="r1",
        agent_name="triage",
        mode=RunMode.INVOKE,
    )


async def test_supervisor_routes_and_answers_end_to_end(monkeypatch):
    """classify → route → dispatch(echo specialist) → resolve → answer, run through the engine."""
    # The classifier returns 'billing', so the request routes to billing_specialist.
    monkeypatch.setattr(
        models_module, "resolve_model", lambda *a, **k: FakeListChatModel(responses=["billing"])
    )

    specialists = {
        "billing_specialist": build_agent(AgentSpec(name="billing_specialist", engine="echo")),
        "faq_specialist": build_agent(AgentSpec(name="faq_specialist", engine="echo")),
    }
    spec = AgentSpec(name="triage", engine="langgraph", model="x")
    supervisor = SupervisorAgent(spec, config=_CFG, specialists=specialists)

    engine = LangGraphEngine()
    compiled = engine.build(spec, supervisor)
    ctx = _ctx()
    token = current_run.set(ctx)  # nodes read the RunContext via the contextvar
    try:
        result = await engine.run(compiled, "I have a billing question", ctx)
    finally:
        current_run.reset(token)

    # The billing specialist (an echo agent) handled it — its echo is in the answer.
    assert "echo: I have a billing question" in result.output


# ---- classify model routing (P02 · C5 wiring) -------------------------------------------------

_CHEAP_CLASSIFY_CFG = GraphConfig.model_validate(
    {
        # Deliberately different from the supervisor's own model below, so a test can tell
        # which one the classify node actually ran on.
        "classify": {"model": "openai/gpt-4o-mini", "intents": ["billing", "general"]},
        "routing": {
            "billing": {"specialists": ["billing_specialist"], "strategy": "single"},
            "general": {"specialists": ["faq_specialist"], "strategy": "single"},
            "_default": {"specialists": ["faq_specialist"], "strategy": "single"},
        },
        "conflict_resolver": {"priority": ["billing_specialist", "faq_specialist"]},
    }
)


async def test_classify_runs_on_the_configured_classify_model(monkeypatch):
    """The classify node uses ``cfg.classify.model``, not the supervisor's own model.

    This is the point of a cheap-classifier setup: label the request with a small model,
    then let the specialists answer on a strong one. Before this wiring, ``classify.model``
    was accepted by the config and then silently ignored — the node reused whatever model
    the engine had wired for the agent.
    """
    built: list[str] = []

    def _fake_resolve(model_id: str, **params):
        built.append(model_id)
        return FakeListChatModel(responses=["billing"])

    monkeypatch.setattr(models_module, "resolve_model", _fake_resolve)

    specialists = {
        "billing_specialist": build_agent(AgentSpec(name="billing_specialist", engine="echo")),
        "faq_specialist": build_agent(AgentSpec(name="faq_specialist", engine="echo")),
    }
    spec = AgentSpec(name="triage", engine="langgraph", model="openai/gpt-4o")
    supervisor = SupervisorAgent(spec, config=_CHEAP_CLASSIFY_CFG, specialists=specialists)

    engine = LangGraphEngine()
    engine.build(spec, supervisor)

    # The cheap classify model was resolved, and it is not the supervisor's model.
    assert "openai/gpt-4o-mini" in built
    assert _CHEAP_CLASSIFY_CFG.classify.model != spec.model
