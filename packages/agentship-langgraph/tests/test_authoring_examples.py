"""Demo proof: the authoring examples build + run offline with a fake model.

No network. These load the real shipped example files and run them with a fake
chat model injected in place of the real LiteLLM one, proving the two Phase-01
authoring paths work end to end from the exact files a user would run:

- ``examples/quickstart.yaml`` — the ``single`` template (zero author Python);
- ``examples/graph.yaml`` — the ``graph`` supervisor scaffold template;
- ``examples/deepagents.yaml`` — the ``deepagents`` prebuilt template;
- ``examples/custom/custom.yaml`` — a custom :class:`LangGraphAgent` subclass
  (``examples/custom/agent.py``) reached via ``code:`` (native LangGraph authoring).
"""

from __future__ import annotations

import os
from pathlib import Path

import agentship_langgraph.models as models_module
import pytest
from agentship.runtime import build_agent
from langchain_core.language_models.fake_chat_models import FakeListChatModel

REPO_ROOT = Path(__file__).resolve().parents[3]
QUICKSTART = str(REPO_ROOT / "examples" / "quickstart.yaml")
CUSTOM = str(REPO_ROOT / "examples" / "custom" / "custom.yaml")
GRAPH = str(REPO_ROOT / "examples" / "graph.yaml")
DEEPAGENTS = str(REPO_ROOT / "examples" / "deepagents.yaml")


@pytest.fixture
def fake_model(monkeypatch):
    """Inject a deterministic fake chat model so the examples run offline."""
    fake = FakeListChatModel(responses=["The primary colors are red, blue, and yellow."])
    monkeypatch.setattr(models_module, "resolve_model", lambda *a, **k: fake)
    return fake


async def test_quickstart_single_template_example_runs(fake_model):
    """examples/quickstart.yaml (template: single) builds and answers, zero author code."""
    agent = build_agent(QUICKSTART)
    assert agent.spec.template == "single"
    result = await agent.run("Name the primary colors.")
    assert result.output == "The primary colors are red, blue, and yellow."


async def test_graph_template_example_runs(fake_model):
    """examples/graph.yaml (template: graph) builds the supervisor scaffold and answers.

    Loads the exact shipped file a user would run and drives it offline: the
    coordinator routes to the worker, whose reply is the answer. Proves the graph
    template example is real (not just parseable) end to end with zero author python.
    """
    agent = build_agent(GRAPH)
    assert agent.spec.template == "graph"
    result = await agent.run("Name the primary colors.")
    assert result.output == "The primary colors are red, blue, and yellow."


def test_deepagents_template_example_builds(fake_model):
    """examples/deepagents.yaml (template: deepagents) builds a compiled deep-agent, offline.

    Loads the exact shipped file and builds it with a fake model. A full autonomous
    turn needs a tool-calling model (Phase 03), so this proves the example builds and
    compiles from the file a user would run — mirroring the template's own build proof.
    """
    pytest.importorskip("deepagents")
    agent = build_agent(DEEPAGENTS)
    assert agent.spec.template == "deepagents"
    assert agent.compiled is not None


async def test_custom_build_graph_example_runs(fake_model):
    """examples/custom/custom.yaml (a LangGraphAgent subclass via code:) builds and answers."""
    # The code: reference is repo-root-relative; run from the repo root so it resolves.
    cwd = os.getcwd()
    os.chdir(REPO_ROOT)
    try:
        agent = build_agent(CUSTOM)
        assert agent.spec.name == "custom-assistant"
        result = await agent.run("Name the primary colors.")
        assert result.output == "The primary colors are red, blue, and yellow."
    finally:
        os.chdir(cwd)
