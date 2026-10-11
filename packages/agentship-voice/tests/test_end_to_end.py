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

from agentship_voice.pipeline import build_pipecat_pipeline, processor_order  # noqa: E402
from pipecat.frames.frames import (  # noqa: E402
    EndFrame,
    InputAudioRawFrame,
    TranscriptionFrame,
    TTSTextFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.pipeline.runner import WorkerRunner  # noqa: E402
from pipecat.pipeline.worker import PipelineWorker  # noqa: E402
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

    There is deliberately no VAD stage: Pipecat's ``VADAnalyzer`` is not a ``FrameProcessor``,
    it belongs to the transport. This test had a ``vad`` slot until a demo showed a pipeline
    built that way transcribed nothing at all.
    """
    order = processor_order(
        transport_in="in", stt="stt", agent="agent", tts="tts", witness="w", transport_out="out"
    )
    assert order == ["in", "stt", "agent", "tts", "w", "out"]
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
    pipeline = build_pipecat_pipeline(turn, stt=_FakeEars("my balance"), tts=_FakeMouth(spoken))

    # PipelineWorker, not the deprecated PipelineTask, because production runs a worker and the
    # difference is not cosmetic: a broadcast interruption travels UPSTREAM as an
    # InterruptionWorkerFrame and it is the WORKER that turns it around into the
    # InterruptionFrame the pipeline sees. Driven through a PipelineTask, barge-in silenced the
    # speaker but no processor was ever told, so history kept the words the human never heard.
    task = PipelineWorker(pipeline)
    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(task)
    runner_run = asyncio.create_task(runner.run())

    # The speaking frames are what a VAD emits around an utterance, and they are not decoration:
    # the aggregator starts the agent only once the human's turn is judged OVER, which is the
    # whole point of the turn-taking fix. Without them the transcript just sits in the context --
    # the same way the agent used to answer the moment it heard words, including its own.
    await task.queue_frames(
        [
            UserStartedSpeakingFrame(),
            InputAudioRawFrame(audio=b"\x00" * 640, sample_rate=16000, num_channels=1),
            UserStoppedSpeakingFrame(),
        ]
    )

    # Shutdown is queued AFTER the reply, not alongside the audio. Queuing EndFrame with the
    # input raced it against the agent: the end-of-turn signal and the end-of-session signal
    # arrived together, and the witness could be torn down before TTS reported a word. A real
    # session does not hang up in the same breath as the user's sentence.
    async def _spoke() -> None:
        while not turn.spoken:
            await asyncio.sleep(0.05)

    try:
        await asyncio.wait_for(_spoke(), timeout=15)
    finally:
        await task.queue_frame(EndFrame())
        await asyncio.wait_for(runner_run, timeout=15)

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
    # Compared without whitespace: TTS aggregates the agent's chunks into whole sentences, so
    # the spoken text is the same words re-split, not the same strings. What must hold is that
    # nothing was spoken that the agent did not say.
    squashed = lambda parts: "".join("".join(parts).split())  # noqa: E731
    assert squashed(turn.spoken) in squashed(turn.generated), "only the agent's words are spoken"
    assert len(turn.spoken) == len(set(turn.spoken)), "a sentence must not be recorded twice"


#: Spelled out, for the reason given in _Rambler.stream: a digit before a full stop is not a
#: sentence boundary as far as TTS is concerned.
_WORDS = [f"{n}" for n in ("one", "two", "three", "four", "five", "six", "seven", "eight")] * 20


class _Rambler:
    """An agent that keeps talking, so there is something to cut off."""

    name = "rambler"
    capabilities = EngineCapabilities(streaming=True)

    def build(self, spec, authored=None):
        """Nothing to compile."""
        return object()

    #: Chunks in a full, uninterrupted reply. Small enough to keep the test quick, large
    #: enough that finishing early is unmistakably an interruption.
    CHUNKS = 60

    async def stream(self, compiled, text, ctx):
        """Emit slowly and at length, the way a long answer arrives in practice.

        The sentences end in a WORD, not a digit. Pipecat's sentence splitter deliberately
        ignores a full stop that follows a number, so that "3.14" is not read as two sentences
        -- and a reply of "part 0. part 1. " is therefore one unsplittable sentence that TTS
        buffers to the very end. Written that way, this test saw nothing spoken until the reply
        was already over, and so could never catch it being cut off.
        """
        for index in range(self.CHUNKS):
            yield Event(type="content", data=f"This is sentence number {_WORDS[index]}. ")
            await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_talking_over_the_agent_stops_it() -> None:
    """Barge-in: speaking while the agent is answering cuts the answer off.

    This is the defect the turn-taking rework exists to fix, and it is tested through the real
    aggregator for a reason. Nothing in our own code emits the interruption — the aggregator
    broadcasts it when a user turn starts, and the agent node reacts. A test that pushed an
    ``InterruptionFrame`` by hand would prove only that we handle a frame we also invented, and
    would have passed throughout the period when interruption was never actually raised.
    """
    ENGINES.register("rambler", _Rambler)
    turn = VoiceTurn(build_agent(AgentSpec(name="rambler", engine="rambler", streaming=True)))
    spoken: list[str] = []
    pipeline = build_pipecat_pipeline(
        turn, stt=_FakeEars("tell me everything"), tts=_FakeMouth(spoken)
    )

    # PipelineWorker, not the deprecated PipelineTask, because production runs a worker and the
    # difference is not cosmetic: a broadcast interruption travels UPSTREAM as an
    # InterruptionWorkerFrame and it is the WORKER that turns it around into the
    # InterruptionFrame the pipeline sees. Driven through a PipelineTask, barge-in silenced the
    # speaker but no processor was ever told, so history kept the words the human never heard.
    # Record that the pipeline told the TURN it was cut off, not merely that audio stopped.
    # This is the half that was missing: barge-in silenced the speaker, nothing corrected the
    # conversation, and the agent's next turn believed it had delivered the whole reply.
    cut_off = []
    was_interrupted = turn.interrupted

    async def _noting_the_cut() -> bool:
        changed = await was_interrupted()
        cut_off.append(changed)
        return changed

    turn.interrupted = _noting_the_cut

    task = PipelineWorker(pipeline)
    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(task)
    runner_run = asyncio.create_task(runner.run())
    audio = InputAudioRawFrame(audio=b"\x00" * 640, sample_rate=16000, num_channels=1)

    await task.queue_frames([UserStartedSpeakingFrame(), audio, UserStoppedSpeakingFrame()])

    async def _started_answering() -> None:
        while not spoken:
            await asyncio.sleep(0.05)

    await asyncio.wait_for(_started_answering(), timeout=15)
    said_before = len(spoken)

    # The human starts talking again. This is the barge-in.
    await task.queue_frames([UserStartedSpeakingFrame(), audio])

    async def _stopped_answering() -> None:
        # Two consecutive quiet samples, because one could land between chunks of a reply that
        # is still running. The agent emits every 50ms, so 0.4s of silence means it has stopped.
        while True:
            mark = len(spoken)
            await asyncio.sleep(0.4)
            if len(spoken) == mark:
                return

    try:
        await asyncio.wait_for(_stopped_answering(), timeout=15)
    finally:
        await task.queue_frame(EndFrame())
        await asyncio.wait_for(runner_run, timeout=15)

    assert spoken, "the agent must have started answering before being cut off"
    assert len(spoken) < _Rambler.CHUNKS, "the reply must have been cut off, not run to completion"
    assert len(spoken) >= said_before, "the barge-in must not have rewound what was already said"
    assert cut_off, (
        "the turn must have been told it was cut off, or the agent's next turn will believe it "
        "said things the human never heard"
    )
    amended = turn.transcript(interrupted=True)
    assert "[cancelled by user]" in amended, "the correction must carry the marker"
    assert f"number {_WORDS[_Rambler.CHUNKS - 1]}" not in amended, (
        "history must stop at what was SPOKEN -- recording sentences the human talked over is "
        "the overstatement this correction exists to remove"
    )
