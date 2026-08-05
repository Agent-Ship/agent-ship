"""T5 proof: the shipped ``examples/assistant.yaml`` runs — offline and live.

The offline test loads the real example file and runs it through the LangGraph
engine with a fake chat model injected (no network), proving the example's wiring
end to end. The live test runs the same example through a real ``gpt-4o-mini``,
recorded once and replayed keyless via a cassette — proving the demo works against
a real provider, not just a stub.
"""

from __future__ import annotations

import os
from pathlib import Path

import litellm
import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

import agentship.models as models_module
from agentship.runtime import build_agent

# See tests/test_live_model.py for why these two lines are needed for VCR replay.
litellm.disable_aiohttp_transport = True
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

EXAMPLE = str(Path(__file__).resolve().parent.parent / "examples" / "assistant.yaml")


async def test_assistant_example_runs_offline_with_fake_model(monkeypatch):
    """Loading and running examples/assistant.yaml returns the fake model's answer."""
    fake = FakeListChatModel(responses=["Red, green, and blue are primary colors."])
    monkeypatch.setattr(models_module, "resolve_model", lambda *a, **k: fake)

    agent = build_agent(EXAMPLE)  # builds from the YAML path, engine: langgraph
    assert agent.spec.engine == "langgraph"
    result = await agent.run("Name three primary colors.")
    assert result.output == "Red, green, and blue are primary colors."


@pytest.fixture
def openai_key_for_replay(monkeypatch):
    """Inject a placeholder OpenAI key when none is set, so replay's client builds."""
    if not os.environ.get("OPENAI_API_KEY"):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-placeholder-for-replay")


@pytest.mark.vcr
async def test_assistant_example_runs_live_via_cassette(openai_key_for_replay):
    """The example runs against a real gpt-4o-mini (recorded), returning a non-empty answer."""
    agent = build_agent(EXAMPLE)
    result = await agent.run("Name three primary colors.")
    assert isinstance(result.output, str)
    assert result.output.strip() != ""
