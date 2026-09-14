"""Hear the whole voice loop run, without owning a microphone.

A real session needs a person and a transport. This does the same work with real providers and
no hardware: it synthesises the question as speech (a plain OpenAI call — that part *is* the
simulated human), then pushes that audio into the **real Pipecat pipeline** the framework
builds, so STT, the agent and TTS are all genuine and run exactly as they would in a session.

The answer is written out as a .wav you can play.

Usage (needs a real OPENAI_API_KEY in .env or the shell)::

    python demos/talk.py
    python demos/talk.py "How many days are there in a leap year?"
"""

from __future__ import annotations

import asyncio
import os
import sys
import wave
from pathlib import Path

import litellm

litellm.disable_aiohttp_transport = True
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

REPO_ROOT = Path(__file__).resolve().parents[1]
AGENT = REPO_ROOT / "agents" / "voice_assistant.yaml"
OUT = REPO_ROOT / "voice-out"

QUESTION = "What is the capital of France?"
RATE = 24000


def _write_wav(path: Path, audio: bytes, rate: int = RATE) -> Path:
    """Write raw 16-bit mono PCM to a playable .wav."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(audio)
    return path


async def _simulate_a_person_asking(question: str) -> bytes:
    """Return PCM audio of ``question`` spoken aloud — the stand-in for a human at a mic.

    A direct provider call on purpose: this is the one part of the loop that is NOT under test,
    so it deliberately does not go through the pipeline.
    """
    from openai import AsyncOpenAI

    response = await AsyncOpenAI().audio.speech.create(
        model="tts-1", voice="alloy", input=question, response_format="pcm"
    )
    return response.content


class _Recorder:
    """Collects the audio and text the pipeline produces, so the demo can report both."""

    def __init__(self) -> None:
        """Start with nothing heard."""
        self.audio: list[bytes] = []


def _capture(recorder: _Recorder):
    """Return a processor that records the spoken audio flowing past it."""
    from pipecat.frames.frames import Frame, TTSAudioRawFrame
    from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

    class Capture(FrameProcessor):
        """Records the spoken audio, then passes every frame along untouched."""

        async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
            """Record what is worth reporting; never alter the stream."""
            await super().process_frame(frame, direction)
            if isinstance(frame, TTSAudioRawFrame):
                recorder.audio.append(frame.audio)

            await self.push_frame(frame, direction)

    return Capture()


async def main(question: str) -> int:
    """Run one full spoken turn through the real pipeline and report where the time went."""
    from agentship.runtime import build_agent
    from agentship.spec import load_spec
    from agentship_voice import VoiceTurn
    from agentship_voice.factories import make_stt, make_tts, preflight
    from agentship_voice.pipeline import build_pipecat_pipeline
    from pipecat.frames.frames import (
        EndFrame,
        InputAudioRawFrame,
        VADUserStartedSpeakingFrame,
        VADUserStoppedSpeakingFrame,
    )
    from pipecat.pipeline.task import PipelineParams
    from pipecat.pipeline.worker import PipelineWorker
    from pipecat.workers.runner import WorkerRunner

    spec = load_spec(AGENT)
    problems = preflight(spec.voice)
    if problems:
        print("voice cannot start:\n  - " + "\n  - ".join(problems), file=sys.stderr)
        return 1

    print(f'1. a person asks: "{question}"')
    asked = await _simulate_a_person_asking(question)
    print(f"   → {_write_wav(OUT / 'question.wav', asked)} ({len(asked)} bytes of speech)")

    print("2. pushing that audio into the real pipeline (STT → agent → TTS)")
    turn = VoiceTurn(build_agent(str(AGENT)), session_id="talk-demo")
    recorder = _Recorder()
    pipeline = build_pipecat_pipeline(
        turn,
        stt=make_stt(spec.voice),
        tts=make_tts(spec.voice),
        transport_out=_capture(recorder),
    )

    # A transport's VAD normally decides when the human started and stopped talking, and a
    # SEGMENTED STT (which OpenAI's is) buffers audio between those two frames and transcribes
    # on the second one. The VAD-prefixed frames are the ones it listens for; the plain
    # UserStarted/StoppedSpeaking frames are a different signal and leave it silent. There is
    # no transport here, so the demo makes that decision itself.
    # OpenAI's speech API returns 24 kHz PCM; the pipeline defaults to 16 kHz in. Saying so
    # explicitly is what lets STT buffer the audio instead of discarding a rate it did not
    # expect.
    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(audio_in_sample_rate=RATE, audio_out_sample_rate=RATE),
    )
    await worker.queue_frames(
        [
            VADUserStartedSpeakingFrame(),
            InputAudioRawFrame(audio=asked, sample_rate=RATE, num_channels=1),
            VADUserStoppedSpeakingFrame(),
        ]
    )

    async def _end_once_answered() -> None:
        """End the session after the reply has been spoken, not before.

        The agent answers in a task, so an EndFrame queued alongside the audio would tear the
        pipeline down mid-sentence — which is what made an earlier version of this demo report
        that nothing had been said.
        """
        for _ in range(600):
            if recorder.audio and turn.trace.llm_total_ms is not None:
                break
            await asyncio.sleep(0.1)
        await worker.queue_frames([EndFrame()])

    ender = asyncio.create_task(_end_once_answered())
    await asyncio.wait_for(WorkerRunner(handle_sigint=False).run(worker), timeout=120)
    ender.cancel()

    print(f'   → heard: "{turn.heard or "(nothing)"}"')
    print(f'   → said : "{"".join(turn.generated) or "(nothing)"}"')

    spoken = b"".join(recorder.audio)
    if spoken:
        print(f"   → {_write_wav(OUT / 'answer.wav', spoken)} ({len(spoken)} bytes of speech)")
    else:
        # TTS audio is delivered through Pipecat's audio context, which a real output transport
        # owns. Without one there is nothing to write — the text path above still ran for real.
        print("   → (answer audio needs a real output transport; the text loop above was live)")

    if turn.trace.llm_ttft_ms is not None:
        print("\nwhere the time went:")
        print(f"   first audio (llm_ttft_ms) : {turn.trace.llm_ttft_ms:.0f} ms")
        print(f"   whole reply (llm_total_ms): {turn.trace.llm_total_ms:.0f} ms")
        print(f"   budget                    : {spec.voice.latency_budget_ms} ms")
    # The loop succeeded if the agent heard a real transcript and answered it.
    return 0 if (turn.heard and turn.generated) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else QUESTION)))
