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

from agentship.errors import CapabilityError
from agentship.spec import VoiceSpec

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
        LLMFullResponseEndFrame,
        LLMFullResponseStartFrame,
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
                #
                # The TYPE is the signal: Pipecat sends partials as InterimTranscriptionFrame,
                # which is a TextFrame and deliberately NOT a TranscriptionFrame, so anything
                # arriving here is already final. Gating on the `finalized` flag instead looks
                # equivalent and is not — it defaults to False and a segmented STT never sets
                # it, so every real transcript was silently skipped and the agent never ran.
                # A VAD that trips on a door slam produces an empty turn. It must cost nothing:
                # no agent call, no model spend, no reply to a sound.
                if not frame.text.strip():
                    logger.debug("empty transcript — no agent call")
                    return
                await self._interrupt()  # a new utterance supersedes any reply still running
                self._reply = self.create_task(self._answer(frame.text))
                return

            await self.push_frame(frame, direction)

        async def _answer(self, text: str) -> None:
            """Stream the agent's reply downstream, one chunk at a time.

            Each chunk is pushed as it arrives rather than joined first, so TTS can start on
            the opening clause while the model is still producing the rest — the difference
            between a reply that begins in a moment and one that begins after a silence.
            """
            # A TTS service aggregates text and needs to know where one reply starts and ends:
            # without these markers it buffers the whole answer waiting for more and speaks
            # nothing at all. The agent's chunks are an LLM response, so they are framed as one.
            await self.push_frame(LLMFullResponseStartFrame())
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
            finally:
                # Closes the response even when the turn failed or was cut off, so a barge-in
                # never leaves TTS waiting for the end of a reply that is not coming.
                await self.push_frame(LLMFullResponseEndFrame())

        async def _interrupt(self) -> None:
            """Cancel the in-flight reply, if any, and wait for it to actually stop."""
            if self._reply is None or self._reply.done():
                self._reply = None
                return
            await self.cancel_task(self._reply)
            self._reply = None

    class SpokenWitness(FrameProcessor):
        """Record what TTS actually spoke, so an interruption clips at the right word."""

        def __init__(self) -> None:
            """Hold the turn whose spoken text this witnesses."""
            super().__init__()
            self._turn = turn
            #: The last text recorded, so the same words are not counted twice. See
            #: process_frame for why a service reports each sentence more than once.
            self._last = None

        async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
            """Confirm text that TTS reports it is speaking; pass everything through.

            Two frame types, because Pipecat emits different ones depending on the service.
            A service with word timestamps emits ``TTSTextFrame`` per word; one without emits
            a plain ``TextFrame`` (that is what ``push_text_frames=True`` means -- checked in
            pipecat's own source, not assumed). Listening for only the first would silently
            record nothing for the majority of services, and "nothing was spoken" is exactly
            the failure this witness exists to prevent.
            """
            await super().process_frame(frame, direction)
            if isinstance(frame, TTSTextFrame | TextFrame) and getattr(
                frame, "will_be_spoken", True
            ):
                # Both frame types carry spoken text, because Pipecat reports a sentence twice:
                # once as the plain TextFrame that `push_text_frames` emits, and again as the
                # TTS-specific TTSTextFrame. Listening for only one misses whichever services
                # do not send it; listening for both records every sentence twice, and an
                # interrupted transcript then claims the agent repeated itself. So the words
                # are recorded once — a repeat of what was just recorded is the same sentence
                # arriving in its other shape, not the agent saying it again.
                spoken = frame.text
                if spoken and spoken != self._last:
                    self._last = spoken
                    self._turn.confirm_spoken(spoken)
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

    async def run(self, turn: VoiceTurn, config: VoiceSpec) -> None:
        """Assemble transport, VAD, STT and TTS around ``turn`` and serve until cancelled.

        Every problem with the setup is reported before anything is built. A voice session
        that dies on a missing key halfway through the first sentence fails in the least
        debuggable place there is, and a first run should be one list of things to fix rather
        than run-fix-run-fix.

        Uses Pipecat's worker API rather than ``PipelineTask``/``PipelineRunner``, which are
        deprecated since 1.3 — there is no reason to write new code against a deprecation.
        """
        from pipecat.pipeline.worker import PipelineWorker
        from pipecat.workers.runner import WorkerRunner

        from ..factories import make_stt, make_tts, make_vad, preflight
        from ..pipeline import build_pipecat_pipeline

        problems = preflight(config)
        if problems:
            raise CapabilityError("voice cannot start:\n  - " + "\n  - ".join(problems))

        transport = _build_transport(config, make_vad(config))
        pipeline = build_pipecat_pipeline(
            turn,
            stt=make_stt(config),
            tts=make_tts(config),
            transport_in=transport.input(),
            transport_out=transport.output(),
        )
        logger.info(
            "voice session: %s → agent → %s (interruptions=%s, budget=%dms)",
            config.stt,
            config.tts,
            config.allow_interruptions,
            config.latency_budget_ms,
        )
        runner = WorkerRunner(handle_sigint=False)
        await runner.add_workers(PipelineWorker(pipeline))
        await runner.run()


def _build_transport(config: VoiceSpec, vad):
    """Build the transport named by ``config.transport``, with ``vad`` doing the endpointing.

    The VAD belongs to the transport's input parameters rather than to a pipeline stage: it has
    to see raw audio to decide when the human stopped talking, which is upstream of everything
    else. ``allow_interruptions`` is handed over here too, because barge-in is a property of the
    transport's turn-taking, not something a downstream processor can retrofit.
    """
    if config.transport == "websocket":
        from pipecat.transports.websocket.server import (
            WebsocketServerParams,
            WebsocketServerTransport,
        )

        return WebsocketServerTransport(
            params=WebsocketServerParams(
                audio_in_enabled=True, audio_out_enabled=True, vad_analyzer=vad
            )
        )
    raise CapabilityError(
        f"unsupported voice transport {config.transport!r} — available: websocket"
    )
