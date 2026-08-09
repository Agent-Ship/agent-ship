"""Real-provider streaming proof: the demo streams multiple tokens, replayed keyless.

This is the demo's streaming proof — it replaces the zero-dependency echo slice as
the *streaming* demonstration. It loads the demo's own ``agents/streaming.yaml``
through the installed AgentShip framework and streams one turn against a real
``gpt-4o-mini``, asserting the answer arrives as **more than one** ``content``
event (genuine token-by-token streaming from a real provider), reassembling to a
non-empty answer, ended by a single terminal ``done``. The streaming HTTP
round-trip is recorded once (credentials redacted) into ``tests/cassettes/`` and
replayed on every later run with **no key**.

To (re-)record the cassette, with a real key present:

    set -a; source ../agentship/.env; set +a
    pytest tests/test_streaming.py --record-mode=once

Then verify it replays keyless:

    env -u OPENAI_API_KEY pytest -q
"""

from __future__ import annotations

from pathlib import Path

import pytest
from agentship import build_agent

# The demo's own streaming agent — loaded by path, exactly as `agentship run
# --stream` would. Transport / cost-map setup and the `openai_key_for_replay`
# fixture live in conftest.py, shared with the other cassette-backed slices.
AGENT = str(Path(__file__).resolve().parents[1] / "agents" / "streaming.yaml")


@pytest.mark.vcr
async def test_demo_streams_multiple_real_tokens(openai_key_for_replay):
    """The demo streaming agent yields >1 content chunk from a real model (via cassette)."""
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
