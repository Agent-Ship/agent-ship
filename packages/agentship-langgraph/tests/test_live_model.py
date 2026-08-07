"""T4 live proof: a real ``gpt-4o-mini`` turn, recorded once and replayed keyless.

This is the phase's end-to-end proof that the wiring reaches a real provider:
``build_agent`` → LangGraph engine → LiteLLM → OpenAI. ``@pytest.mark.vcr`` records
the HTTP round-trip once (with a key, auth header redacted by ``vcr_config``) into
``tests/cassettes/`` and, on every later run, replays it with **no key** — so CI
verifies the live path without secrets. Recording is done deliberately:

    set -a; source ../agent-ship/.env; set +a
    pytest tests/test_live_model.py --record-mode=once
"""

from __future__ import annotations

import os

import litellm
import pytest
from agentship.runtime import build_agent
from agentship.spec import AgentSpec

# LiteLLM's async path defaults to an aiohttp transport that vcrpy (httpx/urllib3
# based) cannot intercept. Forcing the plain httpx transport lets the cassette
# both record and replay the real round-trip.
litellm.disable_aiohttp_transport = True

# Keep the cost map local so no extra HTTP fetch pollutes the cassette (and so
# replay never reaches for githubusercontent).
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")


@pytest.fixture
def openai_key_for_replay(monkeypatch):
    """Ensure an OpenAI key is set so the SDK client constructs during replay.

    The OpenAI SDK refuses to build a client without a key — even to replay a
    recorded response. When no real key is present (CI / keyless replay), inject a
    harmless placeholder; VCR matches on URI + body, not the (redacted) auth
    header, so replay is unaffected. When a real key *is* present (recording), it
    is left untouched.
    """
    if not os.environ.get("OPENAI_API_KEY"):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-placeholder-for-replay")


@pytest.mark.vcr
async def test_real_gpt_4o_mini_returns_a_non_empty_answer(openai_key_for_replay):
    """A real gpt-4o-mini turn returns a non-empty string answer (replayed via cassette)."""
    agent = build_agent(
        AgentSpec(
            name="assistant",
            engine="langgraph",
            model="openai/gpt-4o-mini",
            prompt="You are a concise assistant. Answer in one short sentence.",
        )
    )
    result = await agent.run("Name three primary colors.")
    assert isinstance(result.output, str)
    assert result.output.strip() != ""
