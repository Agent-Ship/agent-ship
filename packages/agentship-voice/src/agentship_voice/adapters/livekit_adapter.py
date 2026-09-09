"""Host an AgentShip agent as the LLM inside a LiveKit ``AgentSession``.

LiveKit does not have a pipeline node to drop an agent into. It has an ``AgentSession`` with
slots — ``stt=``, ``vad=``, ``llm=``, ``tts=`` — and the agent goes in the ``llm`` slot. So
what we implement is ``llm.LLM``: given a chat context, return a stream of ``ChatChunk``.

That is a genuinely different shape from Pipecat's frame processor, which is why the seam
these two share had to be smaller than a pipeline node (see :mod:`agentship_voice.turn`).

**Who witnesses what was spoken.** On Pipecat we add a processor after TTS, because nothing
else reports it. LiveKit already does this work: it emits ``conversation_item_added`` with a
``ChatMessage`` that carries ``interrupted`` and content **already truncated to what was
actually spoken**. So here the adapter subscribes rather than measures — using the
framework's own answer instead of a second, worse one of our own.
"""

from __future__ import annotations

import logging

from ..config import VoiceConfig
from ..turn import VoiceTurn
from .base import VoiceAdapter

logger = logging.getLogger("agentship.voice")


def _build_llm(turn: VoiceTurn):
    """Return an ``llm.LLM`` subclass bound to ``turn``.

    Built inside a function for the same reason as the Pipecat processors: importing this
    package must not require the framework, so the base classes are resolved on use.
    """
    from livekit.agents import llm

    class _AgentShipStream(llm.LLMStream):
        """Streams one AgentShip turn into LiveKit's chunk channel."""

        async def _run(self) -> None:
            """Run the agent for the newest user message and emit its reply as chunks.

            LiveKit hands the whole chat context, but AgentShip keeps its own conversation
            through the session id — the same short-term memory the REST path uses. So only
            the latest user message is taken; replaying LiveKit's history as well would give
            the agent the conversation twice.
            """
            spoken_to = _latest_user_text(self._chat_ctx)
            if not spoken_to:
                return  # nothing was said; emit no chunks rather than an empty assistant turn

            async for chunk in turn.say(spoken_to):
                self._event_ch.send_nowait(
                    llm.ChatChunk(
                        id=self._llm._next_chunk_id(),
                        delta=llm.ChoiceDelta(role="assistant", content=chunk),
                    )
                )

    class AgentShipLLM(llm.LLM):
        """An AgentShip agent in the shape LiveKit's ``AgentSession`` expects."""

        def __init__(self) -> None:
            """Hold a counter so each emitted chunk carries a distinct id."""
            super().__init__()
            self._chunks = 0

        def _next_chunk_id(self) -> str:
            """Return a per-stream unique chunk id."""
            self._chunks += 1
            return f"agentship-{self._chunks}"

        def chat(self, *, chat_ctx, tools=None, conn_options=None, **kwargs):
            """Return the stream that runs one AgentShip turn.

            Tool arguments are accepted and ignored: tools belong to the agent's own spec and
            run inside the engine, so letting LiveKit also inject them would give the model
            two competing tool sets for one turn.
            """
            from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS

            return _AgentShipStream(
                self,
                chat_ctx=chat_ctx,
                tools=tools or [],
                conn_options=conn_options or DEFAULT_API_CONNECT_OPTIONS,
            )

    return AgentShipLLM


def _latest_user_text(chat_ctx) -> str:
    """Return the text of the newest user message in ``chat_ctx``, or ``""``."""
    for item in reversed(getattr(chat_ctx, "items", [])):
        if getattr(item, "role", None) == "user":
            return (getattr(item, "text_content", None) or "").strip()
    return ""


class LiveKitAdapter(VoiceAdapter):
    """Run an AgentShip agent inside a LiveKit ``AgentSession``."""

    name = "livekit"

    def missing_dependency(self) -> str | None:
        """Return an install line when LiveKit Agents is absent, else ``None``."""
        try:
            import livekit.agents  # noqa: F401
        except ImportError:
            return (
                "voice.framework is 'livekit' but livekit-agents is not installed — "
                'pip install "agentship-voice[livekit]"'
            )
        return None

    def host(self, turn: VoiceTurn) -> object:
        """Return the ``llm.LLM`` to pass as ``AgentSession(llm=...)``."""
        return _build_llm(turn)()

    def witness(self, session, turn: VoiceTurn) -> None:
        """Subscribe to the session so what LiveKit actually spoke reaches ``turn``.

        LiveKit truncates an interrupted assistant message to the words that played, so this
        confirms against the framework's own record rather than a second measurement of ours.
        Call once, after the session is created and before it starts.
        """

        def _on_item(event) -> None:
            """Record an assistant message as spoken."""
            item = getattr(event, "item", None)
            if item is None or getattr(item, "role", None) != "assistant":
                return
            text = getattr(item, "text_content", None) or ""
            if text:
                turn.confirm_spoken(text)

        session.on("conversation_item_added", _on_item)

    async def run(self, turn: VoiceTurn, config: VoiceConfig) -> None:
        """Assemble a room session around ``turn`` and serve until cancelled."""
        raise NotImplementedError(
            "LiveKitAdapter.run lands with the transport task — host() and witness() work now."
        )
