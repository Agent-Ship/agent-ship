"""T3 (Phase 1b) demo proof: the tuned + local-model examples build offline.

No network: these load the real ``examples/tuned.yaml`` and ``examples/local.yaml``
and build them with a fake model injected in place of the real LiteLLM one. They
assert the tuning mechanism reaches the provider call — ``tuned.yaml``'s
temperature/max_tokens and ``local.yaml``'s ``api_base`` thread into
``resolve_model`` — and that a local-model build never demands ``OPENAI_API_KEY``.
"""

from __future__ import annotations

from pathlib import Path

import agentship_langgraph.models as models_module
import pytest
from agentship.runtime import build_agent
from langchain_core.language_models.fake_chat_models import FakeListChatModel

EXAMPLES_DIR = Path(__file__).resolve().parents[3] / "examples"
TUNED = str(EXAMPLES_DIR / "tuned.yaml")
LOCAL = str(EXAMPLES_DIR / "local.yaml")


@pytest.fixture
def capture_resolve(monkeypatch):
    """Patch resolve_model to record its (model, kwargs) and return a fake model.

    Keeps every build offline while letting a test read exactly which params the
    example threaded into the provider call.
    """
    calls: dict = {}
    fake = FakeListChatModel(responses=["ok"])

    def fake_resolve(model, **kwargs):
        """Record the resolve call and hand back a deterministic fake model."""
        calls["model"] = model
        calls["kwargs"] = kwargs
        return fake

    monkeypatch.setattr(models_module, "resolve_model", fake_resolve)
    return calls


def test_tuned_example_threads_generation_params(capture_resolve):
    """examples/tuned.yaml builds and threads temperature + max_tokens to the model."""
    agent = build_agent(TUNED)
    assert agent.spec.engine == "langgraph"
    assert capture_resolve["kwargs"]["temperature"] == 0.2
    assert capture_resolve["kwargs"]["max_tokens"] == 256


def test_local_example_threads_api_base(capture_resolve):
    """examples/local.yaml builds and threads api_base (the local Ollama endpoint)."""
    agent = build_agent(LOCAL)
    assert agent.spec.model == "ollama/llama3"
    assert capture_resolve["kwargs"]["api_base"] == "http://localhost:11434"


async def test_local_example_needs_no_openai_key(capture_resolve, monkeypatch):
    """Building + running the local example never demands OPENAI_API_KEY.

    The offline fake model answers with no credential; asserting the run succeeds
    with no key set proves the local path carries no hosted-provider key demand.
    """
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    agent = build_agent(LOCAL)
    result = await agent.run("hi")
    assert result.output == "ok"
