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


def _real_worker():
    """A real ``PipelineWorker`` over an empty pipeline, with its queue captured.

    Deliberately NOT a stand-in with a `queue_frames` method. The first version of this test
    used one, and it passed while the production call raised
    "'Pipeline' object has no attribute 'queue_frames'" on every greeting — the double had the
    method the real object lacked, so the test confirmed the mistake instead of catching it.
    Wrapping the genuine class means a greeting queued the wrong way cannot pass here.
    """
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.worker import PipelineWorker

    worker = PipelineWorker(Pipeline([]))
    queued: list = []

    async def capture(frames):
        queued.extend(frames)

    worker.queue_frames = capture
    return worker, queued


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

    worker, queued = _real_worker()
    await _greet(worker, "Hello, how can I help?")

    kinds = [type(frame) for frame in queued]
    assert kinds == [LLMFullResponseStartFrame, TextFrame, LLMFullResponseEndFrame]
    assert queued[1].text == "Hello, how can I help?"


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


def test_the_greeting_is_queued_on_something_that_can_queue_frames() -> None:
    """Guards the exact mistake: `Pipeline` has `queue_frame`, not `queue_frames`.

    Greeting through the pipeline raised "'Pipeline' object has no attribute 'queue_frames'"
    and killed every session with a greeting at its first breath. This pins the API difference
    itself, so a future refactor that passes the pipeline back in fails here rather than in
    somebody's microphone.
    """
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.worker import PipelineWorker

    assert not hasattr(Pipeline([]), "queue_frames"), (
        "if Pipeline ever grows queue_frames, this guard can go — until then the greeting "
        "must be queued on the worker"
    )
    assert hasattr(PipelineWorker(Pipeline([])), "queue_frames")
