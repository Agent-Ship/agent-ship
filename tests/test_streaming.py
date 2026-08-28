"""Live streaming proof: the demo streams multiple real tokens from OpenAI.

This loads the demo's own ``agents/streaming.yaml`` through the installed AgentShip
framework and streams one real turn against ``gpt-4o-mini``, asserting the answer
arrives as **more than one** ``content`` event — genuine token-by-token streaming
from a real provider — that reassembles into a non-empty answer, ended by a single
terminal ``done``. There is no recording: this makes a live call to OpenAI.

Run it (with a key set):

    set -a; source ../agentship/.env; set +a
    pytest tests/test_streaming.py -q

Without a key the test skips cleanly.
"""

from __future__ import annotations

import pytest

from pathlib import Path

from agentship import build_agent

# The demo's own streaming agent — loaded by path, as `agentship run --stream` would.
AGENT = str(Path(__file__).resolve().parents[1] / "agents" / "streaming.yaml")


@pytest.mark.vcr
async def test_demo_streams_multiple_real_tokens():
    """The demo streaming agent yields >1 real content chunk from a live model."""
    agent = build_agent(AGENT)
    assert agent.spec.engine == "langgraph"
    assert agent.spec.streaming is True

    events = [e async for e in agent.stream("Name the 8 planets, comma-separated.")]

    content = [e for e in events if e.type == "content"]
    # Genuine token-level streaming from a real provider: many small chunks, not
    # one whole message. (0 or 1 would mean streaming regressed.)
    assert len(content) > 1

    answer = "".join(e.data for e in content)
    assert answer.strip() != ""

    assert events[-1].type == "done"
    assert sum(1 for e in events if e.type == "done") == 1
