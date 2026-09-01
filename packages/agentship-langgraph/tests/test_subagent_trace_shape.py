"""A supervisor turn records each model call ONCE.

Sub-agent spans nest under the supervisor now, which means parent and member share one
observer. LangChain propagates a parent invoke's callbacks into nested invokes, so the member's
run installing its own tracing callback makes two callbacks watch the same model call — and the
span, its token counts and its cost are all recorded twice.

Double-counted cost is worse than no cost: it looks like data and it is wrong. This pins the
count.
"""

from __future__ import annotations

import agentship_langgraph.models as models_module
from agentship.observability import SpanKind
from agentship.observability.recorder import RecordingObserver
from agentship.runtime import RunnableAgent, build_agent
from agentship.spec import AgentSpec
from agentship_langgraph.engine import LangGraphEngine
from agentship_langgraph.templates.graph_config import GraphConfig
from agentship_langgraph.templates.graph_supervisor import SupervisorAgent
from langchain_core.language_models.fake_chat_models import FakeListChatModel

_CFG = GraphConfig.model_validate(
    {
        "classify": {"model": "x", "intents": ["billing"]},
        "routing": {
            "billing": {"specialists": ["billing_specialist"], "strategy": "single"},
            "_default": {"specialists": ["billing_specialist"], "strategy": "single"},
        },
        "conflict_resolver": {"priority": ["billing_specialist"]},
    }
)


def _count(node, kind: SpanKind) -> int:
    """Count spans of ``kind`` anywhere in a recorded tree."""
    n = 1 if node.kind is kind else 0
    return n + sum(_count(child, kind) for child in node.children)


async def test_each_model_call_is_recorded_once(monkeypatch):
    """One classify call + one specialist call = two model spans, not four.

    Counting is the assertion that matters: a tree can be correctly shaped and still
    double-count, which would silently double every token and cost figure downstream.
    """
    monkeypatch.setattr(
        models_module, "resolve_model", lambda *a, **k: FakeListChatModel(responses=["billing"])
    )
    specialists = {
        "billing_specialist": build_agent(
            AgentSpec(name="billing_specialist", engine="langgraph", template="single", model="x")
        )
    }
    spec = AgentSpec(name="triage", engine="langgraph", model="x")
    engine = LangGraphEngine()
    compiled = engine.build(spec, SupervisorAgent(spec, config=_CFG, specialists=specialists))
    observer = RecordingObserver()
    supervisor = RunnableAgent(spec, engine, compiled, observer=observer)

    await supervisor.run("my invoice is wrong", session_id="dup-check")

    roots = observer.roots
    assert len(roots) == 1, f"expected one trace, got {len(roots)}"
    model_spans = _count(roots[0], SpanKind.LLM)
    assert model_spans == 2, (
        f"expected 2 model spans (classify + specialist), got {model_spans} — "
        f"each duplicate double-counts its tokens and cost"
    )
