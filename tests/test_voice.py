"""The voice demo: the same agent, reached through ears and a mouth.

Runs keyless. The providers are stand-ins subclassing Pipecat's real service base classes, so
the pipeline, the frame ordering, the agent node and the spoken-text witness are all genuine —
only the paid provider is replaced. `demos/talk.py` is the live counterpart.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

pytest.importorskip("agentship_voice", reason="needs agentship-voice")
pytest.importorskip("pipecat", reason="needs the [pipecat] extra")

# A `voice:` block is only understood by a framework that ships VoiceSpec. On an older core
# the spec is rejected outright (`extra="forbid"`), so this skips with the reason rather than
# failing — the demo repo is installed against a PINNED framework release, and a demo for an
# unreleased feature must not turn the whole suite red.
from agentship.spec import AgentSpec  # noqa: E402

if "voice" not in AgentSpec.model_fields:
    pytest.skip(
        "the installed agentship-core has no `voice:` support yet; this demo needs the "
        "release that carries VoiceSpec",
        allow_module_level=True,
    )

from agentship.engines.base import ENGINES, EngineCapabilities, Event  # noqa: E402
from agentship.runtime import build_agent  # noqa: E402
from agentship.spec import AgentSpec, load_spec  # noqa: E402
from agentship_voice import VoiceTurn  # noqa: E402
from agentship_voice.pipeline import build_pipecat_pipeline  # noqa: E402
from pipecat.frames.frames import (  # noqa: E402
    EndFrame,
    InputAudioRawFrame,
    TranscriptionFrame,
    TTSTextFrame,
)
from pipecat.pipeline.task import PipelineParams  # noqa: E402
from pipecat.pipeline.worker import PipelineWorker  # noqa: E402
from pipecat.services.stt_service import STTService  # noqa: E402
from pipecat.services.tts_service import TTSService  # noqa: E402
from pipecat.workers.runner import WorkerRunner  # noqa: E402

# One directory down from the other agents, and deliberately so: `agentship serve` reads the
# specs directly under `agents/`, and a voice spec's provider keys are checked before the
# socket binds — so an unset DEEPGRAM_API_KEY there stopped the whole service rather than that
# one agent. The spec is still tested here; see agents/voice/README.md.
AGENT = Path(__file__).resolve().parents[1] / "agents" / "voice" / "agent.yaml"
RATE = 24000


class _Ears(STTService):
    """A stand-in microphone that always hears one fixed sentence."""

    def __init__(self, heard: str) -> None:
        """Remember what this microphone will report hearing."""
        super().__init__()
        self._heard = heard

    async def run_stt(self, audio: bytes):
        """Emit the transcript a real provider would return for this audio."""
        frame = TranscriptionFrame(text=self._heard, user_id="demo", timestamp="now")
        frame.finalized = True
        yield frame


class _Mouth(TTSService):
    """A stand-in speaker that reports what it is asked to say."""

    def __init__(self, said: list[str]) -> None:
        """Collect spoken text so a test can assert on it."""
        super().__init__(push_text_frames=True)
        self.said = said

    async def run_tts(self, text: str, context_id: str):
        """Emit the frame a real TTS service emits as it synthesises."""
        self.said.append(text)
        yield TTSTextFrame(text=text, aggregated_by="word", context_id=context_id)


class _Scripted:
    """A deterministic agent, so the demo asserts the loop rather than a model's mood."""

    name = "voice-demo"
    capabilities = EngineCapabilities(streaming=True)

    def build(self, spec, authored=None):
        """Nothing to compile."""
        return object()

    async def stream(self, compiled, text, ctx):
        """Answer in two clauses, echoing the question so the transcript is traceable."""
        yield Event(type="content", data=f"You asked: {text} ")
        yield Event(type="content", data="The capital of France is Paris.")


def test_the_voice_agent_spec_is_valid() -> None:
    """The demo's own YAML must parse and declare a voice block a reader can follow."""
    spec = load_spec(AGENT)
    assert spec.voice is not None, "the demo agent must declare how to run over audio"
    assert spec.voice.framework in {"pipecat", "livekit"}
    assert spec.voice.allow_interruptions, "the demo should show that you can interrupt it"


@pytest.mark.asyncio
async def test_a_spoken_question_reaches_the_agent_and_is_answered() -> None:
    """Audio in, transcript, agent, spoken answer out — the loop the phase delivers."""
    ENGINES.register("voice-demo", _Scripted)
    turn = VoiceTurn(
        build_agent(AgentSpec(name="demo", engine="voice-demo", streaming=True)),
        session_id="voice-demo",
    )
    said: list[str] = []
    pipeline = build_pipecat_pipeline(turn, stt=_Ears("what is the capital of france"), tts=_Mouth(said))

    worker = PipelineWorker(
        pipeline, params=PipelineParams(audio_in_sample_rate=RATE, audio_out_sample_rate=RATE)
    )
    await worker.queue_frames(
        [InputAudioRawFrame(audio=b"\x00" * 640, sample_rate=RATE, num_channels=1), EndFrame()]
    )
    await asyncio.wait_for(WorkerRunner(handle_sigint=False).run(worker), timeout=30)

    assert turn.heard == "what is the capital of france", "the transcript must reach the agent"
    spoken = "".join(said)
    assert "Paris" in spoken, "the agent's answer must reach the mouth"
    assert turn.trace.llm_ttft_ms is not None, "time-to-first-audio is measured on this path"


@pytest.mark.asyncio
async def test_the_agent_never_speaks_its_own_scratchpad() -> None:
    """Reasoning and tool bookkeeping must never reach TTS — they would be read aloud."""

    class _Thinker(_Scripted):
        """An agent that thinks out loud before answering."""

        name = "voice-thinker"

        async def stream(self, compiled, text, ctx):
            """Emit a reasoning frame, a tool round-trip, then the answer."""
            yield Event(type="reasoning", data="The user wants a capital city. Recall France.")
            yield Event(type="tool_call", data={"tool": "atlas", "args": {"q": "France"}})
            yield Event(type="tool_result", data={"tool": "atlas", "result": "Paris"})
            yield Event(type="content", data="Paris.")

    ENGINES.register("voice-thinker", _Thinker)
    turn = VoiceTurn(build_agent(AgentSpec(name="t", engine="voice-thinker", streaming=True)))
    said: list[str] = []
    pipeline = build_pipecat_pipeline(turn, stt=_Ears("capital of france"), tts=_Mouth(said))

    worker = PipelineWorker(
        pipeline, params=PipelineParams(audio_in_sample_rate=RATE, audio_out_sample_rate=RATE)
    )
    await worker.queue_frames(
        [InputAudioRawFrame(audio=b"\x00" * 640, sample_rate=RATE, num_channels=1), EndFrame()]
    )
    await asyncio.wait_for(WorkerRunner(handle_sigint=False).run(worker), timeout=30)

    spoken = "".join(said)
    assert "Paris." in spoken
    assert "Recall France" not in spoken, "the model's thinking must not be spoken"
    assert "atlas" not in spoken, "tool bookkeeping must not be spoken"
