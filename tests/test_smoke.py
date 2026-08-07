"""Anti-rot smoke test: the demo's agent actually runs against a real model.

This is the whole reason the demo repo exists. It loads the demo's own
``agents/assistant.yaml`` through the *installed* AgentShip framework (the public
``build_agent`` entry point), runs one turn against a real ``gpt-4o-mini``, and
asserts a non-empty answer comes back. The real HTTP round-trip is recorded once
into ``tests/cassettes/`` (credentials redacted) and replayed on every later run
with **no key** — so CI proves the published packages wire up end to end, without
secrets.

To (re-)record the cassette, with a real key present:

    set -a; source ../agentship/.env; set +a
    pytest tests/test_smoke.py --record-mode=once

Then verify it replays keyless:

    env -u OPENAI_API_KEY pytest -q
"""

from __future__ import annotations

import os
from pathlib import Path

import litellm
import pytest
from agentship import build_agent

# LiteLLM's async path defaults to an aiohttp transport that vcrpy (httpx/urllib3
# based) cannot intercept. Forcing the plain httpx transport lets the cassette both
# record and replay the real round-trip.
litellm.disable_aiohttp_transport = True

# Keep the cost map local so no extra HTTP fetch pollutes the cassette (and so
# replay never reaches for githubusercontent).
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

# The demo's own agent — loaded by path, exactly as `agentship run` would.
AGENT = str(Path(__file__).resolve().parents[1] / "agents" / "assistant.yaml")


@pytest.fixture
def openai_key_for_replay(monkeypatch):
    """Inject a placeholder OpenAI key when none is set, so replay's client builds.

    The OpenAI SDK refuses to construct a client without a key — even just to replay
    a recorded response. When no real key is present (CI / keyless replay), inject a
    harmless placeholder; VCR matches on URI + body, not the (redacted) auth header,
    so replay is unaffected. When a real key *is* present (recording), it is left
    untouched.
    """
    if not os.environ.get("OPENAI_API_KEY"):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-placeholder-for-replay")


@pytest.mark.vcr
async def test_demo_assistant_returns_a_non_empty_answer(openai_key_for_replay):
    """The demo assistant, loaded from its YAML, returns a non-empty answer (via cassette)."""
    agent = build_agent(AGENT)
    assert agent.spec.engine == "langgraph"

    result = await agent.run("Give one productivity tip.")

    assert isinstance(result.output, str)
    assert result.output.strip() != ""
