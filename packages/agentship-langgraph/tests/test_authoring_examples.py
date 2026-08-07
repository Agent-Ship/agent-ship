"""Demo proof: the authoring examples build + run offline with a fake model.

No network. These load the real shipped example files and run them with a fake
chat model injected in place of the real LiteLLM one, proving the two Phase-01
authoring paths work end to end from the exact files a user would run:

- ``examples/quickstart.yaml`` — the ``single`` template (zero author Python);
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
