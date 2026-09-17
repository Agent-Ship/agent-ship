"""The vendor-free seam: one spoken turn is text in, spoken text out.

These run with no voice framework, no audio device and no provider key — which is the point
of keeping the seam this small. If these need a framework to pass, the seam has leaked.
"""

from __future__ import annotations

import pytest
from agentship.engines.base import ENGINES, EngineCapabilities, Event
from agentship.runtime import build_agent
from agentship.spec import AgentSpec
from agentship_voice.turn import VoiceTurn


class _ScriptedEngine:
    """An engine that streams a fixed list of events, so a turn's filtering is assertable."""

    name = "scripted"
    capabilities = EngineCapabilities(streaming=True)
    #: Set per-test: the exact events the stream should yield.
    script: list[Event] = []

    def build(self, spec, authored=None):
        """Nothing to compile — the script is the behaviour."""
        return object()

    async def stream(self, compiled, text, ctx):
        """Replay the scripted events for this turn."""
        for event in type(self).script:
            yield event


def _turn(script: list[Event]) -> VoiceTurn:
    """Build a VoiceTurn over an agent whose engine yields ``script``."""
    _ScriptedEngine.script = script
    ENGINES.register("scripted", _ScriptedEngine)
    return VoiceTurn(build_agent(AgentSpec(name="talker", engine="scripted", streaming=True)))


async def _spoken(turn: VoiceTurn, text: str) -> list[str]:
    """Collect everything the turn would send to TTS."""
    return [chunk async for chunk in turn.say(text)]


@pytest.mark.asyncio
async def test_a_turn_streams_the_answer_in_order() -> None:
    """Chunks reach TTS as they arrive, in order — that is what makes first audio fast."""
    turn = _turn(
        [
            Event(type="content", data="Your balance "),
            Event(type="content", data="is twelve pounds."),
        ]
    )
    assert await _spoken(turn, "what's my balance") == ["Your balance ", "is twelve pounds."]


@pytest.mark.asyncio
async def test_a_turn_never_speaks_the_model_thinking_aloud() -> None:
    """``reasoning`` is the model's scratchpad, not the answer, so it must not be spoken.

    A reasoning model emits its thinking as a separate frame precisely so a client can tell
    the two apart. A voice pipeline speaks whatever it is handed, so anything that is not
    the reply has to be dropped here — otherwise the agent reads its own notes to the human.
    """
    turn = _turn(
        [
            Event(type="reasoning", data="The user wants a balance. I should check the account."),
            Event(type="content", data="Twelve pounds."),
        ]
    )
    assert await _spoken(turn, "balance") == ["Twelve pounds."]


@pytest.mark.asyncio
async def test_a_turn_never_speaks_tool_bookkeeping() -> None:
    """Tool calls and their results are plumbing; only the agent's words are spoken."""
    turn = _turn(
        [
            Event(type="tool_call", data={"tool": "lookup", "args": {"id": 1}}),
            Event(type="tool_result", data={"tool": "lookup", "result": "12"}),
            Event(type="content", data="Twelve pounds."),
        ]
    )
    assert await _spoken(turn, "balance") == ["Twelve pounds."]


@pytest.mark.asyncio
async def test_content_shapes_are_normalised_for_the_pipeline() -> None:
    """Engines carry content as a string or under content/text/delta; all reach TTS as text.

    Without this an adapter would need to know each engine's payload shape, and an
    unrecognised one would be spoken as the ``repr`` of a dict.
    """
    turn = _turn(
        [
            Event(type="content", data="bare. "),
            Event(type="content", data={"content": "under content. "}),
            Event(type="token", data={"text": "under text. "}),
            Event(type="content", data={"delta": "under delta."}),
        ]
    )
    assert await _spoken(turn, "hi") == [
        "bare. ",
        "under content. ",
        "under text. ",
        "under delta.",
    ]


@pytest.mark.asyncio
async def test_an_unspeakable_payload_is_dropped_not_spoken() -> None:
    """A payload with no text yields nothing rather than reading a dict at the human."""
    turn = _turn(
        [
            Event(type="content", data={"unexpected": {"nested": 1}}),
            Event(type="content", data="Only this."),
        ]
    )
    assert await _spoken(turn, "hi") == ["Only this."]


@pytest.mark.asyncio
async def test_the_session_id_threads_the_conversation_not_the_utterance() -> None:
    """Every utterance in a session runs on the same session id, so the agent remembers.

    A voice caller expects the agent to know what they said a moment ago. Minting a fresh id
    per utterance would give a fluent agent with no memory of the sentence before.
    """
    seen: list[str | None] = []

    class _Recording(_ScriptedEngine):
        """Records the session id each turn ran under."""

        name = "recording"

        async def stream(self, compiled, text, ctx):
            """Note the session id, then say one thing."""
            seen.append(ctx.session_id)
            yield Event(type="content", data="ok")

    ENGINES.register("recording", _Recording)
    turn = VoiceTurn(
        build_agent(AgentSpec(name="talker", engine="recording", streaming=True)),
        session_id="voice-session-1",
    )
    await _spoken(turn, "first")
    await _spoken(turn, "second")

    assert seen == ["voice-session-1", "voice-session-1"]


@pytest.mark.asyncio
async def test_a_structured_agent_speaks_its_named_field_not_its_json() -> None:
    """``speak_field`` is what makes a schema'd agent speakable.

    Without it a voice pipeline recites the object — braces, quotes and key names — which is
    both unusable and the sort of thing nobody discovers until someone is listening.
    """
    from agentship.spec import VoiceSpec

    _ScriptedEngine.script = [Event(type="content", data={"answer": "Twelve pounds.", "cited": 3})]
    ENGINES.register("scripted", _ScriptedEngine)
    agent = build_agent(
        AgentSpec(
            name="talker",
            engine="scripted",
            streaming=True,
            voice=VoiceSpec(speak_field="answer"),
        )
    )
    assert await _spoken(VoiceTurn(agent), "balance?") == ["Twelve pounds."]


@pytest.mark.asyncio
async def test_an_interrupted_turn_corrects_history_to_what_was_heard() -> None:
    """The marker has to reach the conversation, not just be constructible.

    ``transcript()`` built the right string from the start and nothing ever called it, so
    history kept the full intended reply: the model then believed it had said a sentence the
    human heard three words of, and opened the next turn with "as I mentioned…".
    """
    amended: list[str] = []

    class _Recording:
        """An agent that records what its history was corrected to."""

        async def stream(self, text, caller=None, session_id=None):
            """Two clauses, only one of which will be confirmed as spoken."""
            yield Event(type="content", data="One. ")
            yield Event(type="content", data="Two.")

        async def amend_history(self, text, *, caller=None, session_id=None):
            """Record the correction instead of writing to a checkpoint store."""
            amended.append(text)
            return True

    turn = VoiceTurn(_Recording())
    [chunk async for chunk in turn.say("go")]
    turn.confirm_spoken("One. ")  # only the first clause actually played

    assert await turn.interrupted() is True
    assert amended == ["One. [cancelled by user]"]


@pytest.mark.asyncio
async def test_an_interruption_before_the_first_word_corrects_nothing() -> None:
    """Nothing was heard, so there is no overstatement to walk back.

    Writing a bare marker over a reply that never started would itself misreport the turn.
    """

    class _Silent:
        """An agent whose reply was cut off before any audio played."""

        async def stream(self, text, caller=None, session_id=None):
            """Produce a reply that never reaches the speaker."""
            yield Event(type="content", data="One. ")

        async def amend_history(self, text, *, caller=None, session_id=None):
            """Fail loudly if called — it must not be."""
            raise AssertionError("history must not be corrected when nothing was spoken")

    turn = VoiceTurn(_Silent())
    [chunk async for chunk in turn.say("go")]
    assert await turn.interrupted() is False
