"""Demo slice: the `template: single` assistant answers a real question.

This is the whole reason the demo repo exists. It loads the demo's own
``agents/assistant.yaml`` through the *installed* AgentShip framework (the public
``build_agent`` entry point), runs one turn against ``gpt-4o-mini``, and asserts a
real, non-empty answer comes back.

The turn is a genuine provider round-trip, recorded once into a committed cassette:

    pytest tests/test_smoke.py          # replays the cassette — no key, no spend
    pytest tests/test_smoke.py --live   # calls OpenAI for real

Re-record with ``pytest --live --record-mode=once``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentship import build_agent

# The demo's own agent — loaded by path, exactly as `agentship run` would.
AGENT = str(Path(__file__).resolve().parents[1] / "agents" / "assistant.yaml")


@pytest.mark.vcr
async def test_demo_assistant_returns_a_non_empty_answer():
    """The demo assistant, loaded from its YAML, returns a real non-empty answer."""
    agent = build_agent(AGENT)
    assert agent.spec.engine == "langgraph"

    result = await agent.run("Give one productivity tip in one short sentence.")

    assert isinstance(result.output, str)
    assert result.output.strip() != ""
