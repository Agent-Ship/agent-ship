"""The LiveKit wrapper: the agent in the session's LLM slot.

Skipped when the ``[livekit]`` extra is absent. These prove the framework-specific wiring;
the behaviour that must hold everywhere lives in the seam's own tests.
"""

from __future__ import annotations

import pytest
from agentship.engines.base import ENGINES, EngineCapabilities, Event
from agentship.runtime import build_agent
from agentship.spec import AgentSpec
from agentship_voice.turn import CANCELLED_MARKER, VoiceTurn

pytest.importorskip("livekit.agents", reason="needs the [livekit] extra")

from agentship_voice.adapters.livekit_adapter import LiveKitAdapter  # noqa: E402
from livekit.agents import llm as lkllm  # noqa: E402


class _Teller:
    """An engine with a short, checkable reply."""

    name = "teller"
    capabilities = EngineCapabilities(streaming=True)

    def build(self, spec, authored=None):
        """Nothing to compile."""
        return object()

    async def stream(self, compiled, text, ctx):
        """Answer in three pieces so chunking is observable."""
        for part in ("Twelve ", "pounds ", "exactly."):
            yield Event(type="content", data=part)


def _hosted() -> tuple[VoiceTurn, object]:
    """Return (turn, the llm.LLM LiveKit would be given)."""
    ENGINES.register("teller", _Teller)
    turn = VoiceTurn(build_agent(AgentSpec(name="teller", engine="teller", streaming=True)))
    return turn, LiveKitAdapter().host(turn)


def _ctx(*turns: tuple[str, str]) -> lkllm.ChatContext:
    """Build a chat context from (role, text) pairs."""
    ctx = lkllm.ChatContext.empty()
    for role, text in turns:
        ctx.add_message(role=role, content=text)
    return ctx


@pytest.mark.asyncio
async def test_the_agent_is_a_livekit_llm() -> None:
    """``host()`` returns something LiveKit will accept in ``AgentSession(llm=...)``."""
    _turn, hosted = _hosted()
    assert isinstance(hosted, lkllm.LLM)


@pytest.mark.asyncio
async def test_the_reply_streams_back_as_chat_chunks() -> None:
    """The agent's chunks arrive as assistant ``ChatChunk`` deltas, in order."""
    turn, hosted = _hosted()

    stream = hosted.chat(chat_ctx=_ctx(("user", "how much did I spend")))
    chunks = [c async for c in stream]

    assert [c.delta.content for c in chunks] == ["Twelve ", "pounds ", "exactly."]
    assert {c.delta.role for c in chunks} == {"assistant"}
    assert len({c.id for c in chunks}) == 3, "each chunk needs its own id"
    assert turn.trace.llm_ttft_ms is not None


@pytest.mark.asyncio
async def test_only_the_newest_user_message_is_replayed() -> None:
    """AgentShip keeps its own history, so LiveKit's transcript must not be replayed too.

    Passing the whole context through would hand the agent the conversation a second time —
    once from its own session memory and once from LiveKit's — and it would answer as though
    the user had repeated themselves.
    """
    seen: list[str] = []

    class _Recording(_Teller):
        """Records what text each turn actually ran on."""

        name = "recording"

        async def stream(self, compiled, text, ctx):
            """Note the input, then reply."""
            seen.append(text)
            yield Event(type="content", data="ok")

    ENGINES.register("recording", _Recording)
    turn = VoiceTurn(build_agent(AgentSpec(name="r", engine="recording", streaming=True)))
    hosted = LiveKitAdapter().host(turn)

    ctx = _ctx(
        ("user", "what did I spend last week"),
        ("assistant", "Forty pounds."),
        ("user", "and this week"),
    )
    _ = [c async for c in hosted.chat(chat_ctx=ctx)]

    assert seen == ["and this week"], "only the newest user turn runs the agent"


@pytest.mark.asyncio
async def test_an_empty_user_turn_produces_no_assistant_message() -> None:
    """Nothing said means nothing answered — not an empty assistant turn in the transcript."""
    _turn, hosted = _hosted()

    chunks = [c async for c in hosted.chat(chat_ctx=_ctx(("user", "   ")))]

    assert chunks == []


@pytest.mark.asyncio
async def test_livekit_is_the_witness_for_what_was_actually_spoken() -> None:
    """An interrupted assistant message is truncated by LiveKit; we record ITS answer.

    On Pipecat we add a processor after TTS because nothing reports this. LiveKit already
    truncates the message to the words that played, so the adapter subscribes rather than
    taking a second, worse measurement of its own.
    """
    turn, _llm = _hosted()

    class _Session:
        """The slice of AgentSession the adapter uses: an event subscription."""

        def __init__(self) -> None:
            self.handlers: dict[str, object] = {}

        def on(self, name, handler):
            """Register a handler the way AgentSession does."""
            self.handlers[name] = handler

    class _Item:
        """A ChatMessage as LiveKit reports it after an interruption."""

        role = "assistant"
        text_content = "Twelve pounds"  # truncated: "exactly." never played
        interrupted = True

    class _Event:
        item = _Item()

    session = _Session()
    LiveKitAdapter().witness(session, turn)
    session.handlers["conversation_item_added"](_Event())

    assert turn.spoken == ["Twelve pounds"]
    assert turn.transcript(interrupted=True) == f"Twelve pounds {CANCELLED_MARKER}"


@pytest.mark.asyncio
async def test_a_user_message_is_never_recorded_as_spoken_by_the_agent() -> None:
    """The witness listens for assistant items only; the human's own words are not the reply."""
    turn, _llm = _hosted()

    class _Session:
        def __init__(self) -> None:
            self.handlers: dict[str, object] = {}

        def on(self, name, handler):
            self.handlers[name] = handler

    class _UserItem:
        role = "user"
        text_content = "how much did I spend"

    class _Event:
        item = _UserItem()

    session = _Session()
    LiveKitAdapter().witness(session, turn)
    session.handlers["conversation_item_added"](_Event())

    assert turn.spoken == []


def test_a_missing_extra_is_one_actionable_line() -> None:
    """With livekit-agents present there is nothing to report."""
    assert LiveKitAdapter().missing_dependency() is None
