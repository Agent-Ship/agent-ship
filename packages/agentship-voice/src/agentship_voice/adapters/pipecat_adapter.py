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
import re
import time

from agentship.errors import CapabilityError
from agentship.spec import VoiceSpec

from ..turn import VoiceTurn
from .base import VoiceAdapter

logger = logging.getLogger("agentship.voice")


def _latest_user_text(context) -> str:
    """Return what the human just said, from the aggregated conversation.

    The aggregator hands over the whole context, not a transcript, so the newest user message is
    read off the end. Searching backwards rather than taking the last message outright because a
    context can end with a system or tool message, and answering one of those would mean replying
    to our own bookkeeping.
    """
    try:
        messages = context.get_messages()
    except Exception:  # noqa: BLE001 — a context shape we do not know must not kill the turn
        return ""
    for message in reversed(messages or []):
        role = message.get("role") if isinstance(message, dict) else getattr(message, "role", None)
        if role != "user":
            continue
        content = (
            message.get("content") if isinstance(message, dict) else getattr(message, "content", "")
        )
        if isinstance(content, str):
            return content
        # Multimodal content arrives as a list of parts; only the text parts are speakable.
        if isinstance(content, list):
            return " ".join(
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            )
    return ""


def _fallback_text(turn: VoiceTurn) -> str:
    """What to say when a turn fails, from the agent's ``voice:`` block.

    Read per failure rather than captured at build time so an author editing the line does not
    have to think about when it was bound. Falls back to the spec default when a stand-in agent
    carries no voice block at all — something must be said, and silence reads as a dropped call.
    """
    config = getattr(getattr(turn.agent, "spec", None), "voice", None)
    return getattr(config, "fallback_text", None) or "Sorry — something went wrong on my end."


class _TurnClock:
    """When each stage of the current turn happened, shared by the two processors.

    The stage boundaries are observed at opposite ends of the pipeline — the human falling
    silent is seen before STT, the first audio leaving is seen after TTS — so neither processor
    can time a turn alone. This is the one piece of state they genuinely share, kept as its own
    small object rather than smuggled onto the turn, because it is bookkeeping about frames
    rather than anything the agent would recognise.
    """

    def __init__(self) -> None:
        """Start with no turn in progress."""
        #: When the human stopped talking — the moment the clock the listener feels starts.
        self.silence_at: float | None = None
        #: When the first reply text was handed to TTS, which is where synthesis begins.
        self.first_text_at: float | None = None

    def begin(self) -> None:
        """Mark the human as having stopped speaking, starting a fresh turn."""
        self.silence_at = time.monotonic()
        self.first_text_at = None


def _build_processors(turn: VoiceTurn, clock: _TurnClock):
    """Return ``(AgentNodeProcessor, SpokenWitness)`` classes bound to Pipecat's base classes.

    Defined inside a function because the base class comes from Pipecat: at module import
    time the framework may not be installed, and a package whose import fails without an
    optional extra is a package that cannot be introspected, documented or doctored.
    """
    from pipecat.frames.frames import (
        BotStartedSpeakingFrame,
        BotStoppedSpeakingFrame,
        Frame,
        InterimTranscriptionFrame,
        InterruptionFrame,
        LLMContextFrame,
        LLMFullResponseEndFrame,
        LLMFullResponseStartFrame,
        OutputTransportMessageFrame,
        TextFrame,
        TranscriptionFrame,
        TTSTextFrame,
        VADUserStartedSpeakingFrame,
        VADUserStoppedSpeakingFrame,
    )
    from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

    class AgentNodeProcessor(FrameProcessor):
        """Turn a finalised transcript into the agent's spoken reply."""

        def __init__(self) -> None:
            """Hold the turn and the in-flight reply task, if any."""
            super().__init__()
            self._turn = turn
            self._reply: asyncio.Task | None = None
            #: Whether a reply is still owed. Not the same as the task being alive -- see
            #: _interrupt for why the task's state cannot be used for this.
            self._answering = False

        async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
            """Answer a finalised transcript; abandon the reply when the human cuts in."""
            await super().process_frame(frame, direction)

            if isinstance(frame, VADUserStartedSpeakingFrame):
                await self._tell("listening")
            elif isinstance(frame, VADUserStoppedSpeakingFrame):
                # The turn's clock starts at SILENCE, not at the transcript. Everything after
                # this — recognising the words, thinking, synthesising — is time the human is
                # sitting there waiting, and a budget measured from any later point would
                # exclude the stage most likely to have blown it.
                clock.begin()
                await self._tell("thinking")

            if isinstance(frame, InterruptionFrame):
                await self._interrupt()
                await self.push_frame(frame, direction)
                return

            if isinstance(frame, LLMContextFrame):
                # The aggregator upstream has decided the human's turn is OVER and handed us the
                # conversation — which is precisely the job an LLM service does in a Pipecat
                # pipeline, and the role this processor plays.
                #
                # We react to THIS rather than to a raw transcript because the decision of when a
                # turn has ended belongs to the aggregator's turn strategies. Acting on
                # transcripts directly, as this did, meant bypassing them: any sound that
                # produced words started a reply, including the agent's own voice returning
                # through the speakers.
                said = _latest_user_text(frame.context)
                if not said.strip():
                    logger.debug("empty turn — no agent call")
                    return
                # The client is told what was heard HERE, and only here. The turn aggregator
                # upstream CONSUMES transcript frames -- it is what turns them into this
                # context -- so the transcript branch below never runs in a real pipeline and
                # the event it sends never arrives. Removing this as a duplicate of that one
                # left the UI with no record of the human's side of the conversation at all:
                # the agent heard and answered correctly, and the screen stayed blank.
                await self._tell("heard", text=said)
                asr_ms = (
                    (time.monotonic() - clock.silence_at) * 1000
                    if clock.silence_at is not None
                    else None
                )
                await self._interrupt()
                self._reply = self.create_task(self._answer(said, asr_ms))
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
                # What the agent HEARD, sent on to the client: a wrong answer to a MISHEARD
                # question is a different problem from a wrong answer, and they are
                # indistinguishable without showing the transcript.
                # Passed through untouched. Reaching this branch at all means a transcript got
                # past the turn aggregator, which does not happen in a pipeline we build -- the
                # aggregator consumes them. It stays because the processor must not swallow a
                # frame it does not understand, and because a pipeline assembled by hand
                # without an aggregator is a legitimate thing to do.
                await self.push_frame(frame, direction)
                return

            await self.push_frame(frame, direction)

        async def _answer(self, text: str, asr_ms: float | None = None) -> None:
            """Stream the agent's reply downstream, one chunk at a time.

            Each chunk is pushed as it arrives rather than joined first, so TTS can start on
            the opening clause while the model is still producing the rest — the difference
            between a reply that begins in a moment and one that begins after a silence.
            """
            # A TTS service aggregates text and needs to know where one reply starts and ends:
            # without these markers it buffers the whole answer waiting for more and speaks
            # nothing at all. The agent's chunks are an LLM response, so they are framed as one.
            await self.push_frame(LLMFullResponseStartFrame())
            # Tracks whether a reply is still owed, which is NOT the same as whether the reply
            # task is still running -- see _interrupt.
            self._answering = True
            try:
                async for chunk in self._turn.say(text, asr_ms=asr_ms):
                    if clock.first_text_at is None:
                        # Synthesis starts when TTS is first given something to say, so this is
                        # where the TTS stage's clock starts — not when the reply finished.
                        clock.first_text_at = time.monotonic()
                    await self.push_frame(TextFrame(chunk))
            except asyncio.CancelledError:
                raise
            except Exception:
                # A failed turn must not take down the session: the human is mid-conversation
                # and a dead pipeline is worse than an apology.
                logger.exception("agent turn failed")
                await self.push_frame(TextFrame(_fallback_text(self._turn)))
                # The apology was spoken, so the turn is finished and there is nothing to walk
                # back -- a failed reply is not an interrupted one.
                self._answering = False
            else:
                # Cleared here and in the failure branch, never in `finally`: `finally` also
                # runs when the task is CANCELLED, which is the one case where a reply is still
                # owed and the flag must survive for _interrupt to see.
                self._answering = False
            finally:
                # Closes the response even when the turn failed or was cut off, so a barge-in
                # never leaves TTS waiting for the end of a reply that is not coming.
                await self.push_frame(LLMFullResponseEndFrame())
                # Send the turn's timings downstream so a client can show where the time went.
                # After the end frame: this is a report about the turn, not part of it.
                worst = self._turn.trace.biggest_contributor()
                await self._tell(
                    "timings",
                    trace=self._turn.trace.as_dict(),
                    # Named here rather than recomputed in the browser: the rule for "which
                    # stage to optimise next" belongs with the trace, not in every client.
                    biggest={"stage": worst[0], "ms": worst[1]} if worst else None,
                )

        async def _tell(self, kind: str, **data) -> None:
            """Send one UI event to the client.

            ``OutputTransportMessageFrame`` because it is the only non-audio frame the output
            transport hands to a serializer; anything else is dropped without a word.
            """
            await self.push_frame(OutputTransportMessageFrame(message={"type": kind, **data}))

        async def _interrupt(self) -> None:
            """Cancel the in-flight reply, if any, and correct history to what was heard.

            The correction runs only for a reply that was genuinely cut off. A turn that had
            already finished has nothing to walk back — everything it generated was spoken —
            and rewriting history there would replace a complete reply with the same text plus
            a marker saying it was interrupted, which is simply false.
            """
            # Deliberately NOT `self._reply.done()`. Pipecat's own FrameProcessor cancels the
            # tasks a processor created as soon as an interruption is broadcast, and
            # `super().process_frame` runs before this does -- so by the time we look, the reply
            # task is already finished. Reading it meant every real barge-in took the
            # "nothing to walk back" path: the speaker went quiet, and history kept the
            # sentences the human had talked over as though the agent had delivered them.
            #
            # `_answering` records what we actually need to know -- whether a reply was still
            # owed -- independently of who cancelled the task or when.
            if not self._answering:
                self._reply = None
                return
            self._answering = False
            if self._reply is not None and not self._reply.done():
                await self.cancel_task(self._reply)
            self._reply = None
            # After the cancel, so `spoken` holds everything the witness confirmed before the
            # audio stopped — that is the clip point, and it is only final once TTS has stopped.
            await self._turn.interrupted()

    class SpokenWitness(FrameProcessor):
        """Record what TTS actually spoke, so an interruption clips at the right word."""

        def __init__(self) -> None:
            """Hold the turn whose spoken text this witnesses."""
            super().__init__()
            self._turn = turn
            #: The last text recorded, so the same words are not counted twice. See
            #: process_frame for why a service reports each sentence more than once.
            self._last = None

        async def _tell(self, kind: str, **data) -> None:
            """Send one UI event to the client; see the node's method of the same name."""
            await self.push_frame(OutputTransportMessageFrame(message={"type": kind, **data}))

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
            if isinstance(frame, BotStartedSpeakingFrame):
                # First audio out: the end of the only latency measurement a human can feel.
                # Recorded here, after TTS, because this processor is the first thing in the
                # graph that knows sound is actually leaving — a timer stopped before the
                # transport would be measuring our intent rather than their experience.
                now = time.monotonic()
                if clock.first_text_at is not None:
                    self._turn.trace.tts_ms = (now - clock.first_text_at) * 1000
                if clock.silence_at is not None:
                    self._turn.trace.total_ms = (now - clock.silence_at) * 1000
                await self._tell("speaking")
            elif isinstance(frame, BotStoppedSpeakingFrame):
                await self._tell("idle")
            # TranscriptionFrame and InterimTranscriptionFrame are BOTH TextFrames, so a
            # streaming recogniser's partials sail straight into this check and get recorded
            # as things the agent said. With Deepgram the transcript read "said: Is the
            # capital" before the agent had spoken a word — the human's own sentence,
            # attributed to the agent, and counted as spoken for the barge-in clip point.
            heard_not_spoken = isinstance(frame, TranscriptionFrame | InterimTranscriptionFrame)
            if (
                isinstance(frame, TTSTextFrame | TextFrame)
                and not heard_not_spoken
                and getattr(frame, "will_be_spoken", True)
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
                    await self._tell("said", text=spoken)
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
        # One clock per hosted turn, shared by both processors: they observe opposite ends of
        # the same stopwatch, and a turn's timings are only complete when both have reported.
        agent_node, witness = _build_processors(turn, _TurnClock())
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
            # The `voice:` block, which is what configures turn-taking. Omitted here at first,
            # and the omission was invisible: the pipeline fell back to Pipecat's defaults, so
            # every turn-taking setting an agent declared -- barge-in on or off, how many words
            # count as an interruption, whether a greeting is protected -- was silently ignored
            # in every real session while the unit tests, which pass a config, all passed.
            config=config,
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
    else.

    Barge-in is NOT configured here, despite what this docstring claimed for a long while.
    Pipecat 1.8 has no transport switch for it; it is a property of the turn strategies -- see
    :func:`~agentship_voice.pipeline.turn_taking`.
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


def _words(text: str) -> list[str]:
    """Split text into comparable lowercase words, punctuation discarded.

    Compared on words rather than characters because a recogniser and a synthesiser never agree
    on punctuation or casing: the agent writes "Twelve pounds." and the recogniser hears
    "twelve pounds". Matching the raw strings would find nothing.
    """
    return [word for word in re.split(r"[^\w']+", text.lower()) if word]


def _build_echo_guard(turn: VoiceTurn):
    """Return the ``EchoGuard`` class, bound to Pipecat's base class.

    Defined inside a function for the same reason as the processors above: the base class comes
    from Pipecat, which may not be installed at module import time.
    """
    from pipecat.frames.frames import (
        BotStartedSpeakingFrame,
        BotStoppedSpeakingFrame,
        Frame,
        InterimTranscriptionFrame,
        TranscriptionFrame,
    )
    from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

    class EchoGuard(FrameProcessor):
        """Drop transcripts that are the agent's own voice coming back through the microphone.

        This exists because browser echo cancellation is not sufficient on its own. With speakers
        rather than headphones, the agent's output reaches the microphone, the recogniser
        transcribes it faithfully, and the turn aggregator upstream has no way to tell it from
        speech: a log from a real session shows a *sixteen word* final transcript arriving while the
        agent was mid-sentence, which comfortably passes any minimum-word floor and broadcast an
        interruption. The agent interrupted itself with its own sentence, and the conversation
        filled with its own words attributed to the human.

        A word floor cannot fix that, because the echo is not short. What distinguishes echo from
        speech is not its length but its CONTENT: we know exactly what the agent is saying, because
        the witness downstream records every word TTS reports speaking. A transcript made of those
        words, arriving while those words are being spoken, is the speaker feeding back.

        Placed between the recogniser and the turn aggregator deliberately. Rejecting it later is
        too late — the aggregator broadcasts the interruption the moment it believes a turn has
        started, so by the time the agent node is handed a turn, its reply has already been
        cancelled and there is nothing left to save.
        """

        #: How much of a transcript must be words the agent is currently speaking before it is
        #: treated as echo. Not 1.0, because a recogniser mishears and drops words, so an exact
        #: match is rare; not much lower, because a human agreeing with the agent ("yes, twelve
        #: pounds") genuinely does repeat its words and must still be able to interrupt.
        OVERLAP = 0.6

        def __init__(self, turn: VoiceTurn) -> None:
            """Hold the turn whose spoken words are the reference signal."""
            super().__init__()
            self._turn = turn
            self._bot_speaking = False

        async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
            """Pass everything through, except a transcript that is the agent's own voice."""
            await super().process_frame(frame, direction)

            if isinstance(frame, BotStartedSpeakingFrame):
                self._bot_speaking = True
            elif isinstance(frame, BotStoppedSpeakingFrame):
                self._bot_speaking = False

            if self._bot_speaking and isinstance(
                frame, TranscriptionFrame | InterimTranscriptionFrame
            ):
                heard = _words(frame.text)
                # Only the tail of what has been spoken: a long reply would otherwise accumulate
                # enough vocabulary to match almost anything the human says.
                spoken = set(_words(" ".join(self._turn.spoken[-3:])))
                if heard and spoken:
                    overlap = sum(1 for word in heard if word in spoken) / len(heard)
                    if overlap >= self.OVERLAP:
                        logger.debug(
                            "echo rejected (%.0f%% of %d words are the agent's own): %r",
                            overlap * 100,
                            len(heard),
                            frame.text[:80],
                        )
                        return

            await self.push_frame(frame, direction)

    return EchoGuard(turn)
