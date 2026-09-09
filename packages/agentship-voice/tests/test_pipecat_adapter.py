"""The Pipecat wrapper: a finalised transcript in, spoken text out, interruptible.

Skipped entirely when the ``[pipecat]`` extra is absent — the seam's own tests carry the
behaviour that must hold everywhere, and these prove only the framework-specific wiring.
"""

from __future__ import annotations

import asyncio

import pytest
from agentship.engines.base import ENGINES, EngineCapabilities, Event
from agentship.runtime import build_agent
from agentship.spec import AgentSpec
from agentship_voice.turn import CANCELLED_MARKER, VoiceTurn

pipecat = pytest.importorskip("pipecat", reason="needs the [pipecat] extra")

from pipecat.frames.frames import (  # noqa: E402
    InterruptionFrame,
    TextFrame,
    TranscriptionFrame,
    TTSTextFrame,
)


def _tts(text: str, *, will_be_spoken: bool = True) -> TTSTextFrame:
    """Build the frame a TTS service emits as it synthesises a piece of text."""
    frame = TTSTextFrame(text=text, aggregated_by="word")
    frame.will_be_spoken = will_be_spoken
    return frame


from agentship_voice.adapters.pipecat_adapter import PipecatAdapter  # noqa: E402
from pipecat.processors.frame_processor import FrameDirection  # noqa: E402


class _Slow:
    """An engine that yields slowly, so a reply can be interrupted mid-flight."""

    name = "slowtalker"
    capabilities = EngineCapabilities(streaming=True)

    def build(self, spec, authored=None):
        """Nothing to compile."""
        return object()

    async def stream(self, compiled, text, ctx):
        """Four clauses with a pause between each."""
        for part in ("One. ", "Two. ", "Three. ", "Four."):
            await asyncio.sleep(0.02)
            yield Event(type="content", data=part)


def _hosted() -> tuple[VoiceTurn, object, object, list]:
    """Return (turn, agent node, witness, frames pushed downstream by the node)."""
    ENGINES.register("slowtalker", _Slow)
    turn = VoiceTurn(build_agent(AgentSpec(name="talker", engine="slowtalker", streaming=True)))
    node, witness = PipecatAdapter().host(turn)

    pushed: list = []

    async def capture(frame, direction=FrameDirection.DOWNSTREAM):
        """Stand in for the next processor in the pipeline."""
        pushed.append(frame)

    node.push_frame = capture
    witness.push_frame = capture
    return turn, node, witness, pushed


def _transcript(text: str, *, final: bool = True) -> TranscriptionFrame:
    """Build a transcription frame the way an STT service would."""
    frame = TranscriptionFrame(text=text, user_id="u1", timestamp="now")
    frame.finalized = final
    return frame


@pytest.mark.asyncio
async def test_a_final_transcript_is_spoken_back_chunk_by_chunk() -> None:
    """The agent's reply reaches TTS as separate frames, not one block at the end."""
    turn, node, _witness, pushed = _hosted()

    await node.process_frame(_transcript("hello"), FrameDirection.DOWNSTREAM)
    await node._reply

    spoken = [f.text for f in pushed if type(f) is TextFrame]
    assert spoken == ["One. ", "Two. ", "Three. ", "Four."]
    assert turn.trace.llm_ttft_ms is not None, "first-audio latency is measured through the node"


@pytest.mark.asyncio
async def test_a_partial_transcript_does_not_run_the_agent() -> None:
    """Partials exist so a UI can show words appearing — acting on them answers too early."""
    _turn, node, _witness, pushed = _hosted()

    await node.process_frame(_transcript("hel", final=False), FrameDirection.DOWNSTREAM)

    assert node._reply is None, "no agent turn should have started"
    assert not [f for f in pushed if type(f) is TextFrame]


@pytest.mark.asyncio
async def test_a_noise_triggered_empty_turn_costs_nothing() -> None:
    """A VAD that trips on a door slam must not spend a model call."""
    _turn, node, _witness, pushed = _hosted()

    await node.process_frame(_transcript("   "), FrameDirection.DOWNSTREAM)

    assert node._reply is None
    assert pushed == [], "an empty turn produces no frames at all"


@pytest.mark.asyncio
async def test_an_interruption_stops_the_reply_and_clips_at_what_was_spoken() -> None:
    """Barge-in cancels generation, and history records only what TTS reported speaking.

    The witness is what makes the clip point honest: it sits after TTS and confirms text as
    it is synthesised, so the marker lands where audio stopped rather than where the agent
    stopped producing.
    """
    turn, node, witness, _pushed = _hosted()

    await node.process_frame(_transcript("hello"), FrameDirection.DOWNSTREAM)
    await asyncio.sleep(0.05)  # let a couple of clauses get out

    # TTS reports what it is actually speaking — only the first two clauses made it.
    for text in ("One. ", "Two. "):
        await witness.process_frame(_tts(text), FrameDirection.DOWNSTREAM)

    await node.process_frame(InterruptionFrame(), FrameDirection.DOWNSTREAM)

    assert node._reply is None, "the in-flight reply must be cancelled, not orphaned"
    assert turn.transcript(interrupted=True) == f"One. Two. {CANCELLED_MARKER}"
    assert "Four" not in turn.transcript(interrupted=True)


@pytest.mark.asyncio
async def test_a_new_utterance_supersedes_the_reply_still_running() -> None:
    """Speaking again mid-reply abandons the old answer instead of interleaving two."""
    _turn, node, _witness, pushed = _hosted()

    await node.process_frame(_transcript("first"), FrameDirection.DOWNSTREAM)
    await asyncio.sleep(0.03)
    first_count = len([f for f in pushed if type(f) is TextFrame])

    await node.process_frame(_transcript("second"), FrameDirection.DOWNSTREAM)
    await node._reply

    assert first_count < 4, "the first reply should not have finished"
    assert node._reply.done()


@pytest.mark.asyncio
async def test_the_witness_ignores_text_that_will_not_be_spoken() -> None:
    """Text TTS flags as not-to-be-spoken never counts as heard."""
    turn, _node, witness, _pushed = _hosted()

    await witness.process_frame(_tts("skipped", will_be_spoken=False), FrameDirection.DOWNSTREAM)

    assert turn.spoken == []


def test_a_missing_extra_is_one_actionable_line() -> None:
    """With pipecat present there is nothing to report; the message names the install."""
    assert PipecatAdapter().missing_dependency() is None
