"""A spoken turn is traced like any other turn, and says what voice cost on top.

The point of these is not that *a* span exists. It is that the voice span **wraps** the ordinary
agent span rather than replacing it: the whole reason the agent is identical on both channels is
lost if its trace is not. A reader should open a voice trace and find the same tree they would
find for a REST call, with one honest layer on top saying what recognition and synthesis added.

Vendor-free throughout — ``RecordingObserver`` builds the same tree the OTel observer does, so
none of this needs an exporter, a collector or a framework.
"""

from __future__ import annotations

import asyncio

import pytest
from agentship.engines.base import ENGINES, EngineCapabilities, Event
from agentship.observability import RecordingObserver, semconv
from agentship.runtime import build_agent
from agentship.spec import AgentSpec, VoiceSpec
from agentship_voice.turn import VoiceTurn


class _ScriptedEngine:
    """An engine that streams a fixed list of events, so a turn's trace is assertable."""

    name = "traced-scripted"
    capabilities = EngineCapabilities(streaming=True)
    script: list[Event] = []

    def build(self, spec, authored=None):
        """Nothing to compile — the script is the behaviour."""
        return object()

    async def stream(self, compiled, text, ctx):
        """Replay the scripted events, pausing so a barge-in has something to interrupt."""
        for event in type(self).script:
            await asyncio.sleep(0.01)
            yield event


def _traced_turn(
    script: list[Event], *, voice: VoiceSpec | None = None, capture: bool = False
) -> tuple[VoiceTurn, RecordingObserver]:
    """Build a VoiceTurn whose agent records its spans, and return both."""
    _ScriptedEngine.script = script
    ENGINES.register("traced-scripted", _ScriptedEngine)
    observer = RecordingObserver(capture_content=capture)
    spec = AgentSpec(
        name="talker",
        engine="traced-scripted",
        streaming=True,
        voice=voice if voice is not None else VoiceSpec(stt="deepgram", tts="cartesia"),
    )
    return VoiceTurn(build_agent(spec, observer=observer)), observer


def _find(node, name: str):
    """Return the first span named ``name`` anywhere in the tree, or ``None``."""
    if node.name == name or node.name.startswith(f"{name} "):
        return node
    for child in node.children:
        found = _find(child, name)
        if found is not None:
            return found
    return None


@pytest.mark.asyncio
async def test_a_spoken_turn_is_the_root_and_the_agent_span_nests_inside_it() -> None:
    """``voice.turn`` wraps ``agent <name>`` — the inner tree is the REST one, untouched."""
    turn, observer = _traced_turn([Event(type="content", data="Paris.")])
    [chunk async for chunk in turn.say("capital of France?")]

    assert len(observer.roots) == 1
    root = observer.roots[0]
    assert root.name == semconv.SPAN_VOICE_TURN
    agent_span = _find(root, semconv.SPAN_AGENT)
    assert agent_span is not None, "the agent span must still be there, nested under the turn"
    assert agent_span is not root


@pytest.mark.asyncio
async def test_the_turn_span_names_the_providers_that_could_be_at_fault() -> None:
    """A voice stack is four vendors deep; the trace has to say which ones were in play."""
    turn, observer = _traced_turn(
        [Event(type="content", data="hi")],
        voice=VoiceSpec(
            stt="deepgram", stt_model="nova-3", tts="cartesia", tts_model="sonic-2", language="en"
        ),
    )
    [chunk async for chunk in turn.say("hello")]

    attrs = observer.roots[0].attrs
    assert attrs[semconv.AS_VOICE_STT] == "deepgram"
    assert attrs[semconv.AS_VOICE_STT_MODEL] == "nova-3"
    assert attrs[semconv.AS_VOICE_TTS] == "cartesia"
    assert attrs[semconv.AS_VOICE_TTS_MODEL] == "sonic-2"
    assert attrs[semconv.AS_VOICE_LANGUAGE] == "en"


@pytest.mark.asyncio
async def test_the_turn_span_carries_the_latency_trace() -> None:
    """The numbers that decide whether a turn felt fast belong on the span, not only in a UI."""
    turn, observer = _traced_turn([Event(type="content", data="hi")])
    [chunk async for chunk in turn.say("hello", asr_ms=120.0)]

    attrs = observer.roots[0].attrs
    assert attrs[semconv.AS_VOICE_ASR_MS] == 120.0
    assert attrs[semconv.AS_VOICE_LLM_TTFT_MS] > 0
    assert attrs[semconv.AS_VOICE_LLM_TOTAL_MS] > 0
    # Named on the span rather than recomputed by every reader — "what do I fix next" is one
    # answer, and it should not be derived three different ways by three different tools.
    assert attrs[semconv.AS_VOICE_SLOWEST_STAGE] in {"asr_ms", "llm_ttft_ms", "llm_total_ms"}


@pytest.mark.asyncio
async def test_a_barge_in_is_recorded_as_an_interruption_not_as_a_failure() -> None:
    """Being interrupted is the feature working. An errored span would bury real faults."""
    turn, observer = _traced_turn(
        [Event(type="content", data=f"part {i} ") for i in range(20)],
    )
    stream = turn.say("tell me everything")
    await anext(stream)
    await stream.aclose()  # the human cuts in

    root = observer.roots[0]
    assert root.attrs[semconv.AS_VOICE_CANCELLED] is True
    assert root.status == "ok", "a barge-in must not mark the turn errored"


@pytest.mark.asyncio
async def test_an_interrupted_turn_still_reports_where_its_time_went() -> None:
    """The interrupted turn is the one worth reading — it is where the human gave up waiting."""
    turn, observer = _traced_turn([Event(type="content", data=f"part {i} ") for i in range(20)])
    stream = turn.say("tell me everything", asr_ms=90.0)
    await anext(stream)
    await stream.aclose()

    assert observer.roots[0].attrs[semconv.AS_VOICE_ASR_MS] == 90.0


@pytest.mark.asyncio
async def test_the_transcript_stays_off_the_span_unless_capture_is_on() -> None:
    """A transcript is exactly as sensitive as a prompt, so it rides the same PHI gate."""
    turn, observer = _traced_turn([Event(type="content", data="your balance is £12")])
    [chunk async for chunk in turn.say("what's my balance")]

    attrs = observer.roots[0].attrs
    assert semconv.OI_INPUT_VALUE not in attrs
    assert semconv.OI_OUTPUT_VALUE not in attrs


@pytest.mark.asyncio
async def test_with_capture_on_the_span_shows_what_was_heard_and_said() -> None:
    """With the gate open, a trace answers "was it misheard?" without a second tool."""
    turn, observer = _traced_turn([Event(type="content", data="Paris.")], capture=True)
    [chunk async for chunk in turn.say("capital of France?")]

    attrs = observer.roots[0].attrs
    assert attrs[semconv.OI_INPUT_VALUE] == "capital of France?"
    assert attrs[semconv.OI_OUTPUT_VALUE] == "Paris."


@pytest.mark.asyncio
async def test_an_agent_with_no_observer_at_all_still_speaks() -> None:
    """Tracing is fail-open: something that cannot be traced runs untraced, not not-at-all.

    A real ``RunnableAgent`` always carries an observer — a no-op one when tracing is off — so
    the case this covers is a bare object standing in for an agent, which is what every other
    test in this package hands the seam.
    """

    class _BareAgent:
        """An agent-shaped object with no observer and no spec."""

        async def stream(self, text, caller=None, session_id=None):
            """Yield one content event and nothing else."""
            yield Event(type="content", data="hi")

    turn = VoiceTurn(_BareAgent())
    assert [chunk async for chunk in turn.say("hello")] == ["hi"]
