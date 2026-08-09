"""Live slice: the `template: single` assistant answers for real.

This is the whole reason the demo repo exists. It loads the demo's own
``agents/assistant.yaml`` through the *installed* AgentShip framework (the public
``build_agent`` entry point), runs one real turn against ``gpt-4o-mini``, and
asserts a real, non-empty answer comes back. There is no recording and no fake
model — this makes a live call to OpenAI.

Run it (with a key set):

    set -a; source ../agentship/.env; set +a
    pytest tests/test_smoke.py -q

Without a key the test skips cleanly.
"""

from __future__ import annotations

from pathlib import Path

from agentship import build_agent
from conftest import requires_live_key

# The demo's own agent — loaded by path, exactly as `agentship run` would.
AGENT = str(Path(__file__).resolve().parents[1] / "agents" / "assistant.yaml")


@requires_live_key
async def test_demo_assistant_returns_a_non_empty_answer():
    """The demo assistant, loaded from its YAML, returns a real non-empty answer."""
    agent = build_agent(AGENT)
    assert agent.spec.engine == "langgraph"

    result = await agent.run("Give one productivity tip in one short sentence.")

    assert isinstance(result.output, str)
    assert result.output.strip() != ""
