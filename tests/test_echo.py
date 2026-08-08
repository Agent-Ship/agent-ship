"""Keyless slice: the `echo` walking-skeleton agent — run and stream.

The zero-dependency ``echo`` engine proves the whole spine (author -> build ->
run/stream -> CLI) with no model, no API key, and no network. These tests load the
demo's own ``agents/echo.yaml`` through the *installed* framework and assert the
real echoed output, both for a one-shot ``run`` and for a streamed turn.
"""

from __future__ import annotations

from pathlib import Path

from agentship import build_agent

AGENT = str(Path(__file__).resolve().parents[1] / "agents" / "echo.yaml")


async def test_echo_run_prints_echo_of_input():
    """agents/echo.yaml runs keyless and returns `echo: <input>`."""
    agent = build_agent(AGENT)
    assert agent.spec.engine == "echo"

    result = await agent.run("hi there")

    assert result.output == "echo: hi there"


async def test_echo_stream_yields_content_then_done():
    """agents/echo.yaml streams keyless: a content event carrying the echo, then done."""
    agent = build_agent(AGENT)

    events = [event async for event in agent.stream("stream me")]

    types = [event.type for event in events]
    assert types == ["content", "done"]
    content = "".join(event.data for event in events if event.type == "content")
    assert content == "echo: stream me"
