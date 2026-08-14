"""Live slice: an agent calls the built-in calculator tool (Phase 03).

Loads ``agents/calculator.yaml`` (``tools: [calculator]``), asks an arithmetic question, and asserts
the real answer contains the correct result — which requires the model to actually invoke the tool.
The supervisor/tool decision log confirms the calculator was called. Live: it calls OpenAI. Without
a key the test skips cleanly.

Run it (with a key set)::

    set -a; source ../agentship/.env; set +a
    pytest tests/test_tools.py -q
"""

from __future__ import annotations

from pathlib import Path

from agentship import build_agent
from conftest import requires_live_key

REPO_ROOT = Path(__file__).resolve().parents[1]
AGENT = str(REPO_ROOT / "agents" / "calculator.yaml")


@requires_live_key
async def test_calculator_agent_uses_the_tool_and_answers():
    """The agent calls the calculator for `12 * 12 + 3` and reports 147."""
    agent = build_agent(AGENT)
    assert agent.compiled.bound_tools == ["calculator"]
    result = await agent.run("What is 12 * 12 + 3? Use the calculator.")
    assert "147" in result.output
