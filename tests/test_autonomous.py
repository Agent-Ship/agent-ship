"""Live slice: an autonomous agent uses a tool for real (Phase 03 C6).

Loads ``agents/autonomous.yaml`` (``template: autonomous`` + ``tools: [calculator]``), asks a
multi-step arithmetic question, and asserts the correct result — which requires the autonomous
agent to run its self-directed loop AND invoke the calculator tool. This was compile-only until
Phase 03 wired tool execution. Live: it calls OpenAI. Skips without a key or without the
``[autonomous]`` extra.

Run it (with a key set)::

    set -a; source ../agentship/.env; set +a
    pytest tests/test_autonomous.py -q
"""

from __future__ import annotations

from pathlib import Path

import pytest
from agentship import build_agent
from conftest import requires_live_key

pytest.importorskip("deepagents", reason="needs agentship-langgraph[autonomous]")

REPO_ROOT = Path(__file__).resolve().parents[1]
AGENT = str(REPO_ROOT / "agents" / "autonomous.yaml")


@requires_live_key
async def test_autonomous_agent_uses_a_tool():
    """The autonomous agent runs its loop and uses the calculator to answer (18*7)+(100/4) = 151."""
    agent = build_agent(AGENT)
    assert agent.compiled.bound_tools == ["calculator"]
    result = await agent.run("What is (18 * 7) + (100 / 4)?")
    assert "151" in result.output
