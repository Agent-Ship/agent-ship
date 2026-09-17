"""Session-level behaviour: how a conversation opens, and how it is guaranteed to end.

These are the two things that are invisible until a deployment is real — an agent that waits in
silence reads as broken, and a session with no ceiling is a resource leak with a human-shaped
excuse. Both are tested away from the transport, because neither is about audio.
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("pipecat", reason="needs the [pipecat] extra")

from agentship_voice.session import _greet, serve_until_done  # noqa: E402


class _CapturingPipeline:
    """Stands in for a pipeline, recording the frames queued into it."""

    def __init__(self) -> None:
        """Start with nothing queued."""
        self.frames: list = []

    async def queue_frames(self, frames) -> None:
        """Record the frames a real pipeline would process."""
        self.frames.extend(frames)


@pytest.mark.asyncio
async def test_a_greeting_is_framed_as_a_complete_response_so_it_is_actually_spoken() -> None:
    """A bare text frame is not enough: TTS aggregates and waits for an explicit end.

    Without the surrounding response frames the greeting sits in the synthesiser's buffer and
    is never spoken — the agent appears to ignore the person who just connected, which is the
    exact impression a greeting exists to prevent.
    """
    from pipecat.frames.frames import (
        LLMFullResponseEndFrame,
        LLMFullResponseStartFrame,
        TextFrame,
    )

    pipeline = _CapturingPipeline()
    await _greet(pipeline, "Hello, how can I help?")

    kinds = [type(frame) for frame in pipeline.frames]
    assert kinds == [LLMFullResponseStartFrame, TextFrame, LLMFullResponseEndFrame]
    assert pipeline.frames[1].text == "Hello, how can I help?"


@pytest.mark.asyncio
async def test_a_session_with_no_ceiling_runs_until_the_human_hangs_up() -> None:
    """``None`` is no limit — the right default for a conversation nobody is timing."""
    finished = False

    async def session() -> None:
        nonlocal finished
        await asyncio.sleep(0.01)
        finished = True

    await serve_until_done(session(), None, "talker")
    assert finished


@pytest.mark.asyncio
async def test_a_session_that_outstays_its_limit_is_closed_rather_than_left_open() -> None:
    """Hitting the ceiling ends the session quietly; it is a policy, not a failure.

    An abandoned tab holds the socket and its provider connections indefinitely, so this is the
    only thing standing between a public deployment and a slow leak.
    """

    async def forever() -> None:
        await asyncio.sleep(30)

    started = asyncio.get_running_loop().time()
    await serve_until_done(forever(), 1, "talker")  # returns rather than raising
    assert asyncio.get_running_loop().time() - started < 5, "the limit was actually enforced"
