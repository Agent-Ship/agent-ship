"""Tests for the echo engine: run echoes input; stream yields chunk(s) then a terminal event."""

from __future__ import annotations

from agentship.runtime import build_agent
from agentship.spec import AgentSpec


async def test_run_echoes_input():
    """run() returns 'echo: <input>' — the real engine transform, not a constant."""
    agent = build_agent(AgentSpec(name="a", engine="echo"))
    result = await agent.run("hi")
    assert result.output == "echo: hi"


async def test_stream_yields_content_then_done():
    """stream() yields >=1 content chunk carrying the echo, then a terminal 'done' event."""
    agent = build_agent(AgentSpec(name="a", engine="echo", streaming=True))
    events = [e async for e in agent.stream("hi")]

    content = [e for e in events if e.type == "content"]
    assert len(content) >= 1
    assert content[0].data == "echo: hi"

    # The stream terminates with exactly one 'done' event, and it is last.
    assert events[-1].type == "done"
    assert sum(1 for e in events if e.type == "done") == 1
