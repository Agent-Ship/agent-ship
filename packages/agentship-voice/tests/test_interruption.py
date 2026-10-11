"""Barge-in is a memory-correctness problem, not only a cancellation one.

When a human cuts the agent off, history must record what they HEARD. Recording what the
agent intended to say leaves the model believing it spoke sentences that never played, and
the next turn opens with "as I mentioned…" about words the human never got.
"""

from __future__ import annotations

import pytest
from agentship.engines.base import ENGINES, EngineCapabilities, Event
from agentship.runtime import build_agent
from agentship.spec import AgentSpec
from agentship_voice.turn import CANCELLED_MARKER, VoiceTurn


class _LongReply:
    """An engine with plenty to say, so there is something left over when it is cut off."""

    name = "longwinded"
    capabilities = EngineCapabilities(streaming=True)

    def build(self, spec, authored=None):
        """Nothing to compile."""
        return object()

    async def stream(self, compiled, text, ctx):
        """Four clauses — enough to be interrupted partway through."""
        for part in ("Your balance is twelve pounds. ", "You spent ", "four pounds ", "on coffee."):
            yield Event(type="content", data=part)


def _turn() -> VoiceTurn:
    """A VoiceTurn over the long-winded agent."""
    ENGINES.register("longwinded", _LongReply)
    return VoiceTurn(build_agent(AgentSpec(name="teller", engine="longwinded", streaming=True)))


@pytest.mark.asyncio
async def test_history_records_what_was_heard_not_what_was_generated() -> None:
    """The cancel marker lands at the AUDIO stop point, not the generation stop point.

    TTS buffers, so text handed to the pipeline is ahead of text a human has heard. Here the
    agent generates three chunks but only two ever play. History must show the two.
    """
    turn = _turn()

    # An adapter pulls chunks into the TTS buffer as fast as they arrive, but audio plays at
    # human speed. So it confirms far fewer than it pulled — that lag IS the buffer, and it is
    # what makes "generated" and "spoken" diverge at the moment of a barge-in.
    buffered = [chunk async for chunk in turn.say("how much did I spend")]
    for chunk in buffered[:2]:
        turn.confirm_spoken(chunk)  # only these two reached the speaker before the human cut in

    assert len(turn.generated) > len(turn.spoken), "the TTS buffer runs ahead of playback"
    assert turn.transcript(interrupted=True) == (
        "Your balance is twelve pounds. You spent " + CANCELLED_MARKER
    )
    assert "coffee" not in turn.transcript(interrupted=True), "unheard words must not be recorded"


@pytest.mark.asyncio
async def test_an_uninterrupted_turn_records_the_whole_reply() -> None:
    """With no interruption the full reply is history — no marker, nothing dropped."""
    turn = _turn()
    async for chunk in turn.say("how much did I spend"):
        turn.confirm_spoken(chunk)

    transcript = turn.transcript()
    assert transcript.endswith("on coffee.")
    assert CANCELLED_MARKER not in transcript


@pytest.mark.asyncio
async def test_being_cut_off_before_any_audio_records_only_the_marker() -> None:
    """Interrupted before a single word played: history says so rather than inventing speech.

    The agent may already have generated a sentence the human never heard at all. Recording
    it would be worse than recording nothing.
    """
    turn = _turn()
    async for _chunk in turn.say("how much"):
        break  # cut off before TTS confirmed anything

    assert turn.spoken == []
    assert turn.transcript(interrupted=True) == CANCELLED_MARKER


@pytest.mark.asyncio
async def test_time_to_first_audio_is_measured_apart_from_total() -> None:
    """``llm_ttft_ms`` is the wait a listener feels; ``llm_total_ms`` is the whole reply.

    Collapsing the two would hide whether the pipeline is overlapped or merely fast — the
    delta between them is the entire value of streaming.
    """
    turn = _turn()
    chunks = [chunk async for chunk in turn.say("how much")]

    assert len(chunks) == 4
    assert turn.trace.llm_ttft_ms is not None, "first-audio latency must be measured"
    assert turn.trace.llm_total_ms is not None
    assert turn.trace.llm_ttft_ms <= turn.trace.llm_total_ms


@pytest.mark.asyncio
async def test_an_unrun_stage_reports_nothing_rather_than_zero() -> None:
    """Stages that do not exist yet are ``None``: zero would be a measurement we never took."""
    turn = _turn()
    async for _ in turn.say("hi"):
        pass

    trace = turn.trace.as_dict()
    assert trace["asr_ms"] is None and trace["tts_ms"] is None
    assert trace["llm_ttft_ms"] is not None


@pytest.mark.asyncio
async def test_the_trace_names_where_the_time_went() -> None:
    """The biggest measured stage is reported, so a latency readout is actionable."""
    turn = _turn()
    async for _ in turn.say("hi"):
        pass
    turn.trace.tts_ms = 999.0

    worst = turn.trace.biggest_contributor()
    assert worst is not None
    assert worst[0] == "tts_ms", "the slowest stage is the one to optimise next"


def test_turning_barge_in_off_reaches_the_strategy() -> None:
    """``allow_interruptions: false`` must actually stop interruptions being raised.

    This asserts on the strategy object because the setting used to reach nothing at all: it
    was read once, written into a log line, and dropped. Both states of the switch behaved
    identically, which is worse than either -- an agent that asked not to be interrupted was,
    and an agent that asked to be had no interruption raised for it.
    """
    pytest.importorskip("pipecat", reason="needs the [pipecat] extra")
    from agentship.spec import VoiceSpec
    from agentship_voice.pipeline import turn_taking

    on, _ = turn_taking(VoiceSpec(allow_interruptions=True))
    off, _ = turn_taking(VoiceSpec(allow_interruptions=False))
    assert on.start[0]._enable_interruptions is True
    assert off.start[0]._enable_interruptions is False


def test_a_greeting_is_protected_from_its_own_echo_and_silence_is_not() -> None:
    """The microphone is muted through an opening greeting, and only then.

    Muting exists so a greeting cannot be cut off by its own sound returning through the
    speakers. Applied to an agent that opens in silence it would swallow the first thing the
    human says, so the two cases must differ.
    """
    pytest.importorskip("pipecat", reason="needs the [pipecat] extra")
    from agentship.spec import VoiceSpec
    from agentship_voice.pipeline import turn_taking

    _, greeted = turn_taking(VoiceSpec(greeting="Hello there."))
    _, silent = turn_taking(VoiceSpec())
    assert greeted, "a greeting must be protected from its own echo"
    assert not silent, "with no greeting there is nothing to protect, and muting loses words"


def test_the_word_floor_is_reachable_from_a_spec() -> None:
    """``interrupt_min_words:`` must change how soon a barge-in is believed.

    The floor was read with ``getattr(config, "interrupt_min_words", None) or 2`` against a
    field that did not exist on ``VoiceSpec``. It therefore sat at 2 for every agent, and the
    one lever on how quickly an interruption is noticed could not be moved -- which matters
    most to anyone who finds barge-in sluggish, because this is the knob they need.
    """
    pytest.importorskip("pipecat", reason="needs the [pipecat] extra")
    from agentship.spec import VoiceSpec
    from agentship_voice.pipeline import turn_taking

    quick, _ = turn_taking(VoiceSpec(interrupt_min_words=1))
    careful, _ = turn_taking(VoiceSpec(interrupt_min_words=4))
    assert quick.start[0]._min_words == 1
    assert careful.start[0]._min_words == 4
    assert turn_taking(VoiceSpec())[0].start[0]._min_words == 2, "and 2 stays the default"
