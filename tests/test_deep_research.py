"""Live slice: deep-research is a REAL model-driven agent, not a hardcoded pipeline.

``agents/deep_research.yaml`` is a plain ReAct single-agent (``template: single``) with the
built-in ``web_search`` tool and ``durability: checkpoint``. The MODEL decides what to do: a
greeting gets a normal reply and triggers NO search; a substantive question gets researched.

This test pins the behaviour the old hardcoded pipeline got wrong — replying to "hi" with a
"go deeper?" research prompt. It captures AgentShip's tool-call log for the turn and asserts the
agent did NOT call ``web_search`` just to say hello, and that it answered in one turn without
pausing. Live: calls OpenAI. Skips without a key.

    set -a; source ../agentship/.env; set +a
    pytest tests/test_deep_research.py -q
"""

from __future__ import annotations

import pytest

import logging
from pathlib import Path

from agentship import build_agent

REPO_ROOT = Path(__file__).resolve().parents[1]
AGENT = str(REPO_ROOT / "agents" / "deep_research.yaml")


class _ToolCallCollector(logging.Handler):
    """Buffers ``agentship.tools`` log lines so a test can see which tools the agent called."""

    def __init__(self) -> None:
        """Capture at INFO — the level tool invocations are logged at."""
        super().__init__(level=logging.INFO)
        self.text = ""

    def emit(self, record: logging.LogRecord) -> None:
        """Append each tool log message to the running transcript."""
        self.text += record.getMessage() + "\n"


@pytest.mark.vcr
async def test_deep_research_chats_without_researching():
    """"hi" gets a normal reply and triggers NO web search — a real agent, not a forced pipeline."""
    agent = build_agent(AGENT)
    collector = _ToolCallCollector()
    tools_logger = logging.getLogger("agentship.tools")
    prev = tools_logger.level
    tools_logger.setLevel(logging.INFO)
    tools_logger.addHandler(collector)
    try:
        result = await agent.run("hi", session_id="demo-deep-hi")
    finally:
        tools_logger.removeHandler(collector)
        tools_logger.setLevel(prev)

    # It answered conversationally in one turn — no pause, real output, and it did NOT search.
    assert result.interrupt is None, "a greeting should not pause"
    assert result.output and result.output.strip(), "a greeting should get a real reply"
    assert "web_search" not in collector.text, "a greeting must not trigger a web search"
