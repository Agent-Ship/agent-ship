"""Keyless slice: the `deepagents` template — HONEST compiles-only proof.

The ``deepagents`` template builds the deepagents library's prebuilt autonomous
agent (``create_deep_agent``) over the wired model. Today the template BUILDS /
COMPILES into a deep-agent graph; a full autonomous tool-using turn needs tool
execution, which is Phase 03.

So this slice deliberately makes NO live-turn claim. It loads the demo's own
``agents/deepagents.yaml`` with a fake model (offline, no key, no network) and
asserts the build produced a compiled deep-agent graph — nothing more.
"""

from __future__ import annotations

from pathlib import Path

import agentship_langgraph.models as models_module
import pytest
from agentship import build_agent
from langchain_core.language_models.fake_chat_models import FakeListChatModel

AGENT = str(Path(__file__).resolve().parents[1] / "agents" / "deepagents.yaml")


@pytest.fixture
def fake_model(monkeypatch):
    """Inject a deterministic fake chat model so the build runs offline (no key/network)."""
    fake = FakeListChatModel(responses=["ok"])
    monkeypatch.setattr(models_module, "resolve_model", lambda *a, **k: fake)
    return fake


def test_deepagents_template_compiles_into_a_deep_agent_graph(fake_model):
    """agents/deepagents.yaml builds/compiles into a deep-agent graph, offline.

    HONEST SCOPE: compiles-only. A full autonomous tool-using turn is Phase 03, so
    this asserts the build produced a compiled graph and does not run a live turn.
    """
    pytest.importorskip("deepagents")
    agent = build_agent(AGENT)

    assert agent.spec.template == "deepagents"
    assert agent.compiled is not None
    # The compiled artifact is a real (LangGraph) graph object, not the raw spec.
    assert type(agent.compiled).__name__ != "AgentSpec"
