"""The whole loop: audio in → transcript → agent → spoken reply, with no key and no mic.

What is faked here is the **provider** — a stand-in STT and TTS subclassing Pipecat's real
service base classes. Everything between them is genuine: the real Pipeline, the real frame
ordering, the real agent node and the real witness. Stubbing our own processors instead would
prove only that the stubs agree with each other.
"""

from __future__ import annotations

import asyncio

import pytest
from agentship.engines.base import ENGINES, EngineCapabilities, Event
from agentship.runtime import build_agent
from agentship.spec import AgentSpec
from agentship_voice.turn import VoiceTurn

pytest.importorskip("pipecat", reason="needs the [pipecat] extra")

from agentship_voice.pipeline import build_pipeline, processor_order  # noqa: E402
from pipecat.frames.frames import (  # noqa: E402
    EndFrame,
    InputAudioRawFrame,
    TranscriptionFrame,
    TTSTextFrame,
)
from pipecat.pipeline.runner import PipelineRunner  # noqa: E402
from pipecat.pipeline.task import PipelineTask  # noqa: E402
from pipecat.services.stt_service import STTService  # noqa: E402
from pipecat.services.tts_service import TTSService  # noqa: E402


class _FakeEars(STTService):
    """Stand-in STT: turns any audio into one fixed, finalised transcript."""

    def __init__(self, heard: str) -> None:
        """Remember what this 'microphone' will claim it heard."""
        super().__init__()
        self._heard = heard

    async def run_stt(self, audio: bytes):
        """Emit the transcript a real provider would produce for this audio."""
        frame = TranscriptionFrame(text=self._heard, user_id="u1", timestamp="now")
        frame.finalized = True
        yield frame


class _FakeMouth(TTSService):
    """Stand-in TTS: reports what it is speaking, exactly as a real service does."""

    def __init__(self, spoken: list[str]) -> None:
        """Collect spoken text so a test can see what reached the speaker."""
        super().__init__(push_text_frames=True)
        self.spoken = spoken

    async def run_tts(self, text: str, context_id: str):
        """Emit the TTSTextFrame a real provider emits as it synthesises."""
        self.spoken.append(text)
        yield TTSTextFrame(text=text, aggregated_by="word", context_id=context_id)


class _Teller:
    """An agent with a short, checkable answer."""

    name = "e2e"
    capabilities = EngineCapabilities(streaming=True)

    def build(self, spec, authored=None):
        """Nothing to compile."""
        return object()

    async def stream(self, compiled, text, ctx):
        """Answer, echoing the question so the transcript is provably what reached the agent."""
        yield Event(type="content", data=f"You asked about {text}. ")
        yield Event(type="content", data="Twelve pounds.")


def _turn() -> VoiceTurn:
    """A VoiceTurn over the answering agent."""
    ENGINES.register("e2e", _Teller)
    return VoiceTurn(build_agent(AgentSpec(name="teller", engine="e2e", streaming=True)))


def test_the_pipeline_order_is_the_cascade() -> None:
    """Ears → agent → mouth → witness, and the witness is AFTER the mouth.

    Order is the design. The witness sits downstream of TTS because that is the only place the
    frame saying what is actually being spoken can be seen; putting it earlier would silently
    record text that was buffered but never heard.
    """
    order = processor_order(
        transport_in="in",
        vad="vad",
        stt="stt",
        agent="agent",
        tts="tts",
        witness="w",
        transport_out="out",
    )
    assert order == ["in", "vad", "stt", "agent", "tts", "w", "out"]
    assert order.index("w") > order.index("tts"), "the witness must observe TTS, not precede it"


def test_absent_stages_are_dropped_so_the_loop_runs_without_a_mic() -> None:
    """A pipeline with no transport and no VAD is still a valid cascade."""
    assert processor_order(stt="stt", agent="agent", tts="tts") == ["stt", "agent", "tts"]


@pytest.mark.asyncio
async def test_audio_in_becomes_a_spoken_reply() -> None:
    """Drive real audio frames through a real pipeline and hear the agent answer.

    This is the loop the phase exists to deliver, minus the providers: audio arrives, STT
    transcribes it, the agent answers, TTS speaks the answer, and the witness records what was
    spoken — all through Pipecat's own Pipeline, with no key and no microphone.
    """
    turn = _turn()
    spoken: list[str] = []
    pipeline = build_pipeline(turn, stt=_FakeEars("my balance"), tts=_FakeMouth(spoken))

    task = PipelineTask(pipeline)
    await task.queue_frames(
        [InputAudioRawFrame(audio=b"\x00" * 640, sample_rate=16000, num_channels=1), EndFrame()]
    )
    await asyncio.wait_for(PipelineRunner(handle_sigint=False).run(task), timeout=15)

    said = "".join(spoken)
    assert "my balance" in said, "the transcript must have reached the agent"
    assert "Twelve pounds." in said, "the agent's answer must have reached TTS"
    assert turn.spoken, "the witness must have recorded what TTS spoke"
    assert turn.trace.llm_ttft_ms is not None, "first-audio latency is measured on the real path"

    # The property that makes barge-in honest: the witness reflects what TTS emitted, which is
    # NOT simply the agent's own text passing through. If these were equal, the witness would be
    # echoing the agent and an interrupted turn would record words nobody heard -- the exact bug
    # the generated/spoken split exists to prevent, and one that hides behind a passing test.
    assert turn.spoken != turn.generated, "the witness must not echo the agent's own frames"
    assert "".join(turn.generated).startswith("".join(turn.spoken)[: len("".join(turn.spoken))]), (
        "spoken text is a prefix of generated text -- speech cannot run ahead of generation"
    )
