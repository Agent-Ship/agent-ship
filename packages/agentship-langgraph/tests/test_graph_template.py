"""T3 proof: the ``graph`` template — a compilable supervisor scaffold, offline.

``template: graph`` gives an author a *fillable* multi-agent starting point: a
minimal but real supervisor ``StateGraph`` that routes a coordinator to one worker,
with ``# TODO(author)`` markers where specialists/tools/routing get filled in. This
wave ships the authoring scaffold only — a full durable multi-agent runtime is Phase
02. These tests prove the scaffold builds and *compiles* against a fake model with no
network, and that it genuinely routes coordinator → worker (not a single node).
"""

from __future__ import annotations

import agentship_langgraph.models as models_module
import pytest
from agentship.runtime import build_agent
from agentship.spec import AgentSpec
from langchain_core.language_models.fake_chat_models import FakeListChatModel


@pytest.fixture
def fake_model(monkeypatch):
    """Inject a deterministic fake chat model so the scaffold runs offline."""
    fake = FakeListChatModel(responses=["worker", "the worker's answer"])
    monkeypatch.setattr(models_module, "resolve_model", lambda *a, **k: fake)
    return fake


def test_graph_template_builds_and_compiles(fake_model):
    """A template: graph spec builds into a compiled graph with no author python."""
    agent = build_agent(
        AgentSpec(
            name="triage",
            engine="langgraph",
            template="graph",
            model="x",
            prompt="Route the request to a specialist.",
        )
    )
    assert agent.compiled is not None


def test_graph_scaffold_routes_coordinator_to_worker(fake_model):
    """The compiled scaffold contains both the coordinator and worker nodes.

    Inspects the compiled graph's nodes: a real supervisor scaffold has a distinct
    ``coordinator`` and ``worker`` node wired coordinator → worker. A single-node
    default build would not, so observing both nodes proves the graph template body
    ran (non-vacuous).
    """
    from agentship_langgraph.templates.graph import build_graph_template

    build_body = build_graph_template(
        AgentSpec(name="triage", engine="langgraph", template="graph", model="x")
    )
    graph = build_body(fake_model, [])
    compiled = graph.compile()
    nodes = set(compiled.get_graph().nodes)
    assert "coordinator" in nodes
    assert "worker" in nodes


async def test_graph_scaffold_runs_end_to_end(fake_model):
    """The scaffold runs a turn end to end: coordinator routes, worker answers.

    With the fake model returning 'worker' (route) then an answer, the run should
    reach the worker and surface its answer — proving the conditional edge routes
    coordinator → worker, the core of the supervisor scaffold.
    """
    agent = build_agent(
        AgentSpec(
            name="triage",
            engine="langgraph",
            template="graph",
            model="x",
            prompt="Route the request.",
        )
    )
    result = await agent.run("help me")
    assert result.output == "the worker's answer"
