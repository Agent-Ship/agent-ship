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

from pathlib import Path

import pytest
from agentship import build_agent

# The demo's own agent — loaded by path, exactly as `agentship run` would. The
# LiteLLM transport / cost-map setup and the `openai_key_for_replay` fixture live
# in conftest.py, shared with the other cassette-backed slices.
AGENT = str(Path(__file__).resolve().parents[1] / "agents" / "assistant.yaml")


@pytest.mark.vcr
async def test_demo_assistant_returns_a_non_empty_answer(openai_key_for_replay):
    """The demo assistant, loaded from its YAML, returns a non-empty answer (via cassette)."""
    agent = build_agent(AGENT)
    assert agent.spec.engine == "langgraph"

    result = await agent.run("Give one productivity tip.")

    assert isinstance(result.output, str)
    assert result.output.strip() != ""
