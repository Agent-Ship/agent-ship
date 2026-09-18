"""Phase 10 conformance cells: the promises the voice channel makes (CONF-VOICE-1..6).

These pin what a *spoken* agent guarantees, as distinct from what the package's unit tests
already cover. The distinction is worth stating, because it decides what belongs here:

* The unit tests prove each part behaves — the witness records, the seam filters, the factories
  resolve.
* These cells prove the **promises a user relies on**: the turn is fast enough to feel like
  conversation, the agent on the phone is the same agent on the API, and a framework swap does
  not change what the human experiences.

Every cell is keyless and offline. What is faked is always the *provider* — stand-ins subclass
Pipecat's real service base classes — never our own code, so the pipeline, the frame ordering,
the agent node, the witness and the trace are all genuine. The latency cell fakes provider
*time* as well, for the same reason a budget test cannot depend on someone's network.
"""

from __future__ import annotations

import asyncio

import pytest
from agentship.engines.base import ENGINES, EngineCapabilities, Event
from agentship.observability import RecordingObserver, semconv
from agentship.runtime import build_agent
from agentship.spec import AgentSpec, VoiceSpec
from agentship_voice.adapters import get_adapter
from agentship_voice.turn import VoiceTurn

pytest.importorskip("pipecat", reason="needs the [pipecat] extra")

from agentship_voice.pipeline import build_pipecat_pipeline  # noqa: E402
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

#: What each faked provider costs, in seconds. Real numbers for a fast 2026 stack: a streaming
#: recogniser finalising shortly after silence, and a low-latency synthesiser's first audio.
#: Hard-coded so the budget is measured against a *stated* provider profile — a cell that used
#: real providers would be measuring somebody's network and would fail on a bad afternoon.
_ASR_TTFB = 0.12
_TTS_TTFB = 0.09


class _SlowEars(STTService):
    """Stand-in STT that takes a realistic time to finalise a transcript."""

    def __init__(self, heard: str) -> None:
        """Remember what this 'microphone' will claim it heard."""
        super().__init__()
        self._heard = heard

    async def run_stt(self, audio: bytes):
        """Emit one finalised transcript, after a provider-shaped delay."""
        await asyncio.sleep(_ASR_TTFB)
        frame = TranscriptionFrame(text=self._heard, user_id="u1", timestamp="now")
        frame.finalized = True
        yield frame


class _SlowMouth(TTSService):
    """Stand-in TTS that takes a realistic time before its first audio."""

    def __init__(self, spoken: list[str]) -> None:
        """Collect spoken text so a cell can see what reached the speaker."""
        super().__init__(push_text_frames=True)
        self.spoken = spoken

    async def run_tts(self, text: str, context_id: str):
        """Emit the frame a real service emits, after a provider-shaped delay."""
        await asyncio.sleep(_TTS_TTFB)
        self.spoken.append(text)
        yield TTSTextFrame(text=text, aggregated_by="word", context_id=context_id)


class _Answerer:
    """An engine with one short answer, so a cell measures the pipeline and not the model."""

    name = "conf-voice"
    capabilities = EngineCapabilities(streaming=True)

    #: The one answer, as the chunks a streaming model would produce. Shared by both channels so
    #: CONF-VOICE-4 compares the same reply rather than two hard-coded strings that happen to
    #: agree — the cell has to be able to *fail* if the paths diverge.
    CHUNKS = ("Twelve pounds. ", "Anything else?")

    def build(self, spec, authored=None):
        """Nothing to compile."""
        return object()

    async def run(self, compiled, text, ctx):
        """Answer in one piece, the way a REST caller receives it."""
        from agentship.engines.base import Result

        return Result(output="".join(self.CHUNKS))

    async def stream(self, compiled, text, ctx):
        """Answer in two chunks, so first-audio can start before generation ends."""
        await asyncio.sleep(0.03)  # a fast model's time to first token
        for chunk in self.CHUNKS:
            yield Event(type="content", data=chunk)
            await asyncio.sleep(0.03)


def _voice_agent(observer=None, voice: VoiceSpec | None = None):
    """Build the agent these cells speak to."""
    ENGINES.register("conf-voice", _Answerer)
    spec = AgentSpec(
        name="teller",
        engine="conf-voice",
        streaming=True,
        voice=voice if voice is not None else VoiceSpec(stt="deepgram", tts="cartesia"),
    )
    return build_agent(spec, observer=observer) if observer else build_agent(spec)


async def _run_one_turn(turn: VoiceTurn, spoken: list[str]) -> float:
    """Drive one audio-in → audio-out turn, returning milliseconds to the first spoken text.

    The pipeline is left running until audio actually appears rather than being handed an
    ``EndFrame`` up front. With providers that take a realistic time to respond, an end frame
    queued behind the audio tears the pipeline down before the transcript is finalised — the
    turn then produces nothing, and the cell would be measuring how fast a pipeline can shut
    down rather than how fast an agent can answer.
    """
    pipeline = build_pipecat_pipeline(turn, stt=_SlowEars("my balance"), tts=_SlowMouth(spoken))
    task = PipelineTask(pipeline)
    runner = asyncio.create_task(PipelineRunner(handle_sigint=False).run(task))
    started = asyncio.get_running_loop().time()
    try:
        await task.queue_frames(
            [InputAudioRawFrame(audio=b"\x00" * 640, sample_rate=16000, num_channels=1)]
        )
        deadline = started + 15
        while not spoken and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.005)
        first_audio_ms = (asyncio.get_running_loop().time() - started) * 1000
        # Let the rest of the reply finish so the trace is complete before anyone reads it.
        await asyncio.sleep(0.2)
        return first_audio_ms
    finally:
        await task.queue_frames([EndFrame()])
        await asyncio.wait_for(runner, timeout=15)


@pytest.mark.asyncio
async def test_conf_voice_1_first_audio_lands_inside_the_budget() -> None:
    """CONF-VOICE-1: time-to-first-audio stays under the spec's ``latency_budget_ms``.

    The budget is the phase's headline promise (DESIGN §8: < 850ms mouth-to-ear) and it is the
    only latency number a human can actually feel — everything after the first syllable is
    covered by them listening. Measured end to end against stated provider TTFBs rather than
    summed from parts, because the whole point of streaming the reply into TTS is that the
    stages **overlap**: a cell that added the stages up would pass a pipeline that had lost
    that overlap entirely.
    """
    agent = _voice_agent()
    turn = VoiceTurn(agent)
    spoken: list[str] = []

    # First audio, not the whole turn: the reply is still being generated when TTS starts, and
    # that gap is exactly what the design bought by streaming.
    ttfa_ms = await _run_one_turn(turn, spoken)

    budget = agent.spec.voice.latency_budget_ms
    assert spoken, "the turn produced no audio at all"
    assert ttfa_ms < budget, (
        f"first audio took {ttfa_ms:.0f}ms against a {budget}ms budget with providers costing "
        f"{(_ASR_TTFB + _TTS_TTFB) * 1000:.0f}ms — the pipeline is adding more than it should, "
        f"or a stage stopped overlapping"
    )


@pytest.mark.asyncio
async def test_conf_voice_2_the_turn_reports_where_its_time_went() -> None:
    """CONF-VOICE-2: every turn returns the per-stage trace, not just a total.

    A total says a turn was slow; only the breakdown says what to do about it. This is a
    contract because the numbers are read by Studio's latency panel and by the ``voice.turn``
    span — a stage silently going unmeasured would show as zero rather than as missing, and
    nobody would go looking for the time.
    """
    turn = VoiceTurn(_voice_agent())
    await _run_one_turn(turn, [])

    trace = turn.trace
    assert trace.llm_ttft_ms is not None, "time to first token is the listener's wait"
    assert trace.llm_total_ms is not None
    assert trace.llm_ttft_ms <= trace.llm_total_ms, "first token cannot follow the last one"
    assert trace.biggest_contributor() is not None, "the trace must name what to optimise next"


@pytest.mark.asyncio
async def test_conf_voice_3_a_spoken_turn_is_traced_like_any_other_turn() -> None:
    """CONF-VOICE-3: the voice span wraps the ordinary agent tree, and names the providers.

    The claim the whole package rests on is that the agent is *the same agent*. That claim is
    checkable in the trace: a voice turn must produce the identical inner tree a REST turn
    produces, with one layer on top accounting for what voice added. A voice span that replaced
    the agent span — or one that appeared as a sibling — would mean the two channels had
    genuinely diverged.
    """
    observer = RecordingObserver()
    turn = VoiceTurn(_voice_agent(observer=observer))
    await _run_one_turn(turn, [])

    assert len(observer.roots) == 1, "one spoken turn is one trace"
    root = observer.roots[0]
    assert root.name == semconv.SPAN_VOICE_TURN
    assert any(child.name.startswith(semconv.SPAN_AGENT) for child in root.children), (
        "the agent span must nest inside the voice turn, not replace it"
    )
    assert root.attrs[semconv.AS_VOICE_STT] == "deepgram"
    assert root.attrs[semconv.AS_VOICE_TTS] == "cartesia"


@pytest.mark.asyncio
async def test_conf_voice_4_the_same_agent_answers_over_rest_and_over_voice() -> None:
    """CONF-VOICE-4: one ``RunnableAgent``, two channels, the same words.

    Not a tautology, because the two paths genuinely differ: voice goes through the seam's
    chunk filtering and a projection step, REST does not. A divergence here would mean the
    voice channel had quietly become its own agent — the thing this architecture exists to
    prevent — and it is the kind of drift that shows up first as "the phone one answers
    differently", which nobody can debug.
    """
    agent = _voice_agent()

    rest = await agent.run("my balance", user_id="conformance")
    spoken = [chunk async for chunk in VoiceTurn(agent).say("my balance")]

    assert spoken, "the voice channel produced nothing to compare"
    assert "".join(spoken) == str(rest.output), (
        "the spoken answer and the REST answer came from the same agent and must match"
    )


@pytest.mark.asyncio
async def test_conf_voice_5_both_frameworks_host_the_same_agent() -> None:
    """CONF-VOICE-5: a framework is a deployment choice, not a different agent.

    Both adapters are asked for the same thing and must accept it. This is the parity that
    makes ``voice.framework`` a genuine switch rather than a fork: an agent written against one
    must be hostable on the other without the author knowing which is in use.

    Framework-specific *behaviour* is proven in each adapter's own suite; what this cell pins is
    that the seam both must satisfy is identical, and that neither has quietly grown a
    requirement the other cannot meet.
    """
    voice = VoiceSpec(framework="pipecat", stt="deepgram", tts="cartesia")
    turn = VoiceTurn(_voice_agent(voice=voice))

    for name in ("pipecat", "livekit"):
        adapter = get_adapter(name)
        assert adapter.name == name
        missing = adapter.missing_dependency()
        if missing:
            continue  # that extra is not installed here; its own suite covers it
        # `host` is the seam every framework must implement over the identical turn object.
        assert adapter.host(turn) is not None, f"{name} could not host the shared turn"


@pytest.mark.asyncio
async def test_conf_voice_6_a_barge_in_records_only_what_was_heard() -> None:
    """CONF-VOICE-6: an interrupted turn contributes what was spoken, plus the marker.

    The promise is about honesty of memory. If history keeps the full intended reply, the next
    turn opens with "as I mentioned…" about a sentence the human heard three words of — the
    model is not hallucinating, it is faithfully reading a record of a conversation that never
    happened. The marker must therefore land at the **audio** stop point, which is why the
    witness sits after TTS and why ``spoken`` is a separate list from ``generated``.
    """
    turn = VoiceTurn(_voice_agent())
    turn.generated = ["Twelve pounds. ", "Anything else?"]
    turn.spoken = ["Twelve pounds. "]  # only the first clause actually played

    transcript = turn.transcript(interrupted=True)
    assert transcript == "Twelve pounds. [cancelled by user]".replace("  ", " ")
    assert "Anything else?" not in transcript, (
        "text that was generated but never heard must not enter history"
    )
