"""Host an AgentShip agent as a node in a Pipecat frame pipeline.

Two processors, because the two things we need to know happen at opposite ends of the TTS
service:

``AgentNodeProcessor`` sits **before** TTS. It turns a finalised transcript into the agent's
reply and pushes that downstream as text to be spoken.

``SpokenWitness`` sits **after** TTS. Pipecat's TTS services emit ``TTSTextFrame`` as they
synthesise, which is the only in-band signal for *this text is actually being spoken* — and
it travels downstream, so a processor placed before TTS can never see it. Without the second
processor the clip point on a barge-in would be "what we handed to TTS", which is ahead of
what the human heard by the whole audio buffer. That is precisely the error the seam's
generated/spoken split exists to prevent, so it would be self-defeating to approximate it.

Both are imported lazily by :class:`PipecatAdapter` so that installing this package without
the ``[pipecat]`` extra costs nothing.
"""

from __future__ import annotations

import asyncio
import logging

from ..config import VoiceConfig
from ..turn import VoiceTurn
from .base import VoiceAdapter

logger = logging.getLogger("agentship.voice")


def _build_processors(turn: VoiceTurn):
    """Return ``(AgentNodeProcessor, SpokenWitness)`` classes bound to Pipecat's base classes.

    Defined inside a function because the base class comes from Pipecat: at module import
    time the framework may not be installed, and a package whose import fails without an
    optional extra is a package that cannot be introspected, documented or doctored.
    """
    from pipecat.frames.frames import (
        Frame,
        InterruptionFrame,
        TextFrame,
        TranscriptionFrame,
        TTSTextFrame,
    )
    from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

    class AgentNodeProcessor(FrameProcessor):
        """Turn a finalised transcript into the agent's spoken reply."""

        def __init__(self) -> None:
            """Hold the turn and the in-flight reply task, if any."""
            super().__init__()
            self._turn = turn
            self._reply: asyncio.Task | None = None

        async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
            """Answer a finalised transcript; abandon the reply when the human cuts in."""
            await super().process_frame(frame, direction)

            if isinstance(frame, InterruptionFrame):
                await self._interrupt()
                await self.push_frame(frame, direction)
                return

            if isinstance(frame, TranscriptionFrame):
                # Only FINAL transcripts run the agent. Partials exist so a UI can show words
                # appearing; acting on them would run the agent several times per sentence and
                # answer a question the human had not finished asking.
                if not getattr(frame, "finalized", True):
                    await self.push_frame(frame, direction)
                    return
                # A VAD that trips on a door slam produces an empty turn. It must cost nothing:
                # no agent call, no model spend, no reply to a sound.
                if not frame.text.strip():
                    logger.debug("empty transcript — no agent call")
                    return
                await self._interrupt()  # a new utterance supersedes any reply still running
                self._reply = asyncio.create_task(self._answer(frame.text))
                return

            await self.push_frame(frame, direction)

        async def _answer(self, text: str) -> None:
            """Stream the agent's reply downstream, one chunk at a time.

            Each chunk is pushed as it arrives rather than joined first, so TTS can start on
            the opening clause while the model is still producing the rest — the difference
            between a reply that begins in a moment and one that begins after a silence.
            """
            try:
                async for chunk in self._turn.say(text):
                    await self.push_frame(TextFrame(chunk))
            except asyncio.CancelledError:
                raise
            except Exception:
                # A failed turn must not take down the session: the human is mid-conversation
                # and a dead pipeline is worse than an apology.
                logger.exception("agent turn failed")
                await self.push_frame(TextFrame("Sorry — something went wrong on my end."))

        async def _interrupt(self) -> None:
            """Cancel the in-flight reply, if any, and wait for it to actually stop."""
            if self._reply is None or self._reply.done():
                self._reply = None
                return
            self._reply.cancel()
            # Awaiting the cancellation matters: returning while the old task is still
            # producing would let a superseded reply push text after the new turn started.
            try:
                await self._reply
            except asyncio.CancelledError:
                pass
            finally:
                self._reply = None

    class SpokenWitness(FrameProcessor):
        """Record what TTS actually spoke, so an interruption clips at the right word."""

        def __init__(self) -> None:
            """Hold the turn whose spoken text this witnesses."""
            super().__init__()
            self._turn = turn

        async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
            """Confirm text that TTS reports it is speaking; pass everything through."""
            await super().process_frame(frame, direction)
            if isinstance(frame, TTSTextFrame) and getattr(frame, "will_be_spoken", True):
                self._turn.confirm_spoken(frame.text)
            await self.push_frame(frame, direction)

    return AgentNodeProcessor, SpokenWitness


class PipecatAdapter(VoiceAdapter):
    """Run an AgentShip agent inside a Pipecat pipeline."""

    name = "pipecat"

    def missing_dependency(self) -> str | None:
        """Return an install line when Pipecat is absent, else ``None``."""
        try:
            import pipecat  # noqa: F401
        except ImportError:
            return (
                "voice.framework is 'pipecat' but pipecat is not installed — "
                'pip install "agentship-voice[pipecat]"'
            )
        return None

    def host(self, turn: VoiceTurn) -> object:
        """Return the pair of processors that put ``turn`` into a Pipecat pipeline.

        A pair rather than one node because the agent and the confirmation of what was spoken
        belong on opposite sides of TTS; see the module docstring.
        """
        agent_node, witness = _build_processors(turn)
        return agent_node(), witness()

    async def run(self, turn: VoiceTurn, config: VoiceConfig) -> None:
        """Assemble transport, VAD, STT and TTS around ``turn`` and serve until cancelled."""
        raise NotImplementedError(
            "PipecatAdapter.run lands with the transport/VAD task — host() is usable now."
        )
