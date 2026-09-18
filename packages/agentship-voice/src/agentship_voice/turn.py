"""The one thing every voice framework needs from an agent: text in, text chunks out.

The phase spec assumed a shared ``AgentNodeProcessor`` — one component both Pipecat and
LiveKit could hold. Against the real 1.8 APIs that component cannot exist, because the two
frameworks want the agent in incompatible shapes:

* **Pipecat** wants a ``FrameProcessor`` with ``process_frame(frame, direction)``, sitting
  as a node in a frame graph.
* **LiveKit** wants an ``llm.LLM`` whose ``chat(chat_ctx=...)`` returns an ``LLMStream`` of
  ``ChatChunk``, sitting in the LLM slot of an ``AgentSession``.

What they genuinely share is much smaller: *given what the human said, stream back what the
agent says*. That is :class:`VoiceTurn`, and it is the entire vendor-free surface of this
package. Each adapter wraps it in its framework's idiom, so the adapters stay thin and the
interesting part — the agent — is identical to the one the REST service runs.

Keeping the seam this small is what lets it be tested with no framework, no audio device and
no provider key: a turn is an async iterator of strings.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager

from agentship.context import Caller
from agentship.observability import SpanKind, semconv
from agentship.runtime import RunnableAgent

from .trace import LatencyTrace

#: Appended to history when the human cuts the agent off. Kept as one constant because an
#: adapter, a test and a transcript reader all have to agree on the exact string. No leading
#: space: spoken chunks usually end with one, and the join below owns the spacing.
CANCELLED_MARKER = "[cancelled by user]"


class VoiceTurn:
    """Run one spoken turn against a :class:`~agentship.runtime.RunnableAgent`.

    Holds the agent and the identity every turn runs as. A voice session authenticates once,
    at connect time, so the caller is fixed for the life of the session rather than proven
    per utterance — unlike HTTP, where every request carries its own credential.
    """

    def __init__(
        self, agent: RunnableAgent, *, caller: Caller | None = None, session_id: str | None = None
    ) -> None:
        """Bind ``agent`` to the identity and conversation this voice session speaks as.

        ``session_id`` threads the whole conversation, not one utterance: a caller expects a
        voice agent to remember what was said a moment ago, and that is the same short-term
        memory the REST path gets from a stable session id.
        """
        self.agent = agent
        self.caller = caller
        self.session_id = session_id
        #: What the human actually said this turn, as STT heard it. Recorded because it is the
        #: input the answer has to be judged against — a wrong answer to a misheard question is
        #: an STT problem, and without this the two are indistinguishable in a log.
        self.heard: str | None = None
        #: Everything the agent produced this turn — including text that was generated but
        #: never reached the speaker because the human cut in.
        self.generated: list[str] = []
        #: Only what an adapter has confirmed actually played. See :meth:`confirm_spoken`.
        self.spoken: list[str] = []
        #: Where this turn's time went. Filled stage by stage; see :mod:`agentship_voice.trace`.
        self.trace = LatencyTrace()

    async def say(self, text: str, *, asr_ms: float | None = None) -> AsyncIterator[str]:
        """Stream the agent's reply to ``text`` as chunks, in the order it produces them.

        ``asr_ms`` is how long recognition took to turn speech into ``text``. It is passed in
        rather than measured here because only the framework saw the audio; the turn is handed a
        finished sentence and has no idea how long the human waited for it. Omitting it loses the
        stage from the trace, which is worse than it sounds — recognition is frequently the
        slowest part of a turn, and a trace without it blames the model for someone else's time.

        Only content is yielded. A voice pipeline speaks what it receives, so anything that
        is not the answer must not reach it: ``reasoning`` frames are the model thinking out
        loud, and ``tool_call``/``tool_result`` are bookkeeping. Passing those through would
        have the agent read its own scratchpad aloud.

        Chunks are yielded as they arrive rather than joined at the end, because time-to-first
        audio is what a listener perceives as latency — TTS can begin on the first clause
        while the model is still producing the rest. ``llm_ttft_ms`` is stamped on the first
        chunk for exactly that reason: it is the number the listener actually waits through.

        Abandoning this iterator (the human interrupted) is a normal ending, not an error. The
        generator simply stops; ``generated`` holds what was produced, and ``spoken`` holds
        only what an adapter confirmed reached the speaker.
        """
        # A session holds ONE VoiceTurn and speaks many times through it, so per-utterance state
        # has to be cleared here. It was not, and the effect was quiet: every turn after the
        # first kept the first turn's `llm_ttft_ms` (the stamp is guarded on it being unset), so
        # the latency panel showed one real measurement and then froze, while `generated` and
        # `spoken` accumulated the whole conversation and the barge-in clip point drifted further
        # from the truth with every exchange.
        self._reset()
        self.trace.asr_ms = asr_ms
        self.heard = text
        started = time.monotonic()
        speak_field = self._config.speak_field if self._config else None
        # The span wraps the whole reply, so the ordinary `agent <name>` span opens inside it and
        # the voice turn becomes the root of the trace. Nothing else changes: the inner tree is
        # the same one a REST call produces, which is the point — the agent is identical, and the
        # trace should show that rather than describing voice as a different system.
        with self._span() as span:
            async for event in self.agent.stream(
                text, caller=self.caller, session_id=self.session_id
            ):
                if event.type not in ("content", "token"):
                    continue
                chunk = _text_of(event.data, speak_field)
                if not chunk:
                    continue
                if self.trace.llm_ttft_ms is None:
                    self.trace.llm_ttft_ms = (time.monotonic() - started) * 1000
                self.generated.append(chunk)
                yield chunk
            self.trace.llm_total_ms = (time.monotonic() - started) * 1000
            self._stamp(span)

    def _reset(self) -> None:
        """Clear everything that belongs to one utterance, keeping the session's identity.

        The split is the point: ``agent``/``caller``/``session_id`` describe the conversation and
        must survive, while the transcript and the timings describe a single turn and must not.
        """
        self.heard = None
        self.generated = []
        self.spoken = []
        self.trace = LatencyTrace()

    async def interrupted(self) -> bool:
        """Correct the conversation to what was actually spoken; ``True`` if history changed.

        Called by an adapter when the human cuts in. Until this existed, :meth:`transcript` built
        the right string and nothing ever read it — the ``[cancelled by user]`` marker was
        constructed, tested, and then dropped on the floor. History kept the full intended reply,
        so the model believed it had said a sentence the human had heard perhaps three words of.

        Does nothing when nothing was spoken yet: an interruption that lands before the first
        word has no overstatement to correct, and writing a bare marker over a reply that never
        started would itself be a lie about what happened.
        """
        if not self.spoken:
            return False
        amend = getattr(self.agent, "amend_history", None)
        if amend is None:
            return False
        return await amend(
            self.transcript(interrupted=True), caller=self.caller, session_id=self.session_id
        )

    @property
    def _config(self):
        """This session's ``voice:`` block, or ``None`` when the agent declares none.

        Read through the agent rather than held as a field: the spec is the single source of
        truth for how this agent speaks, and a copy taken at construction time would be a second
        one to keep in step. Defensive because a test may hand in a stand-in agent.
        """
        return getattr(getattr(self.agent, "spec", None), "voice", None)

    @contextmanager
    def _span(self) -> Iterator[object]:
        """Open this turn's ``voice.turn`` span, or yield a no-op when tracing is off.

        A barge-in closes the span **cleanly**, not as an error. Abandoning the reply raises
        ``GeneratorExit``/``CancelledError`` through this block, and letting that reach the
        observer would mark every interrupted turn as failed — so a conversation where the human
        interrupts normally, which is the whole point of barge-in, would read as a wall of errors
        and bury the turns that genuinely broke. The interruption is recorded as an attribute
        instead, where it belongs.

        Fail-open throughout (§4.7): if there is no observer, or opening the span fails, the turn
        runs untraced rather than not at all.
        """
        observer = getattr(self.agent, "observer", None)
        if observer is None:
            yield _UNTRACED
            return
        manager = observer.span(semconv.SPAN_VOICE_TURN, SpanKind.INTERNAL, self._span_attrs())
        span = manager.__enter__()
        try:
            yield span
        except BaseException as exc:
            if isinstance(exc, GeneratorExit | asyncio.CancelledError):
                span.set_attribute(semconv.AS_VOICE_CANCELLED, True)
                self._stamp(span)
                manager.__exit__(None, None, None)
            else:
                manager.__exit__(type(exc), exc, exc.__traceback__)
            raise
        else:
            manager.__exit__(None, None, None)

    def _span_attrs(self) -> dict[str, object]:
        """Return the opening attributes for the turn span: who is listening and speaking.

        Set at open rather than at close so a turn that is still running — or one that died
        mid-way — still says which providers it was using. The stack is four vendors deep and the
        one at fault is never obvious from the outside.
        """
        config = self._config
        if config is None:
            return {}
        return {
            semconv.AS_VOICE_FRAMEWORK: config.framework,
            semconv.AS_VOICE_STT: config.stt,
            semconv.AS_VOICE_STT_MODEL: config.stt_model,
            semconv.AS_VOICE_TTS: config.tts,
            semconv.AS_VOICE_TTS_MODEL: config.tts_model,
            semconv.AS_VOICE_VOICE_ID: config.voice_id,
            semconv.AS_VOICE_LANGUAGE: config.language,
        }

    def _stamp(self, span) -> None:
        """Write this turn's latency trace and transcript onto ``span``.

        Called at the end of a turn *and* on a barge-in, because an interrupted turn is exactly
        the one worth reading: it is where the numbers explain why the human gave up waiting.

        Content is gated on the observer's ``capture_content`` — a transcript is as sensitive as
        a prompt, so it rides the same PHI switch rather than a second one nobody would remember
        to set.
        """
        trace = self.trace
        span.set_attributes(
            {
                semconv.AS_VOICE_ASR_MS: trace.asr_ms,
                semconv.AS_VOICE_LLM_TTFT_MS: trace.llm_ttft_ms,
                semconv.AS_VOICE_LLM_TOTAL_MS: trace.llm_total_ms,
                semconv.AS_VOICE_TTS_MS: trace.tts_ms,
                semconv.AS_VOICE_TTFA_MS: trace.total_ms,
            }
        )
        worst = trace.biggest_contributor()
        if worst is not None:
            span.set_attribute(semconv.AS_VOICE_SLOWEST_STAGE, worst[0])
        if getattr(getattr(self.agent, "observer", None), "capture_content", False):
            if self.heard:
                span.set_attribute(semconv.OI_INPUT_VALUE, self.heard)
            # What was SPOKEN, falling back to what was generated when nothing is confirmed yet.
            # The distinction matters on an interrupted turn: the trace should show what the
            # human heard, which is the only version of the answer that actually existed.
            said = "".join(self.spoken) or "".join(self.generated)
            if said:
                span.set_attribute(semconv.OI_OUTPUT_VALUE, said)

    def confirm_spoken(self, chunk: str) -> None:
        """Record that ``chunk`` actually reached the speaker.

        An adapter calls this as audio plays, which is later than when the chunk was yielded —
        TTS buffers, so text handed over is not yet text heard. Only the framework knows when
        sound left the speaker, so only the framework can report it.

        This gap is the whole reason the method exists. On a barge-in the history must record
        what the human *heard*, and the difference between generated and spoken is exactly the
        sentence they cut off.
        """
        self.spoken.append(chunk)

    def transcript(self, *, interrupted: bool = False) -> str:
        """Return what this turn should contribute to conversation history.

        When ``interrupted``, that is what was actually **spoken**, followed by
        ``[cancelled by user]`` — never what was merely generated. Recording the full intended
        reply would leave the model believing it said things the human never heard, and the
        next turn opens with "as I mentioned…" about a sentence that was cut off mid-word.

        The marker therefore lands at the audio stop point, not the generation stop point.
        """
        if not interrupted:
            return "".join(self.generated)
        # rstrip so a chunk that ended mid-phrase ("You spent ") does not leave a double
        # space before the marker. The spacing belongs to this join, not to the chunks.
        heard = "".join(self.spoken).rstrip()
        return f"{heard} {CANCELLED_MARKER}" if heard else CANCELLED_MARKER


class _UntracedSpan:
    """The span handed out when no observer is wired — every write is dropped.

    Exists so :meth:`VoiceTurn.say` never needs an ``if span is not None`` around each
    attribute. Tracing being off is the ordinary case for a local ``voice serve``, and the
    turn's own code should not read as though it were an exception.
    """

    def set_attribute(self, key: str, value: object) -> None:
        """Ignore the attribute."""

    def set_attributes(self, attrs) -> None:
        """Ignore the attributes."""


_UNTRACED = _UntracedSpan()


def _text_of(data: object, speak_field: str | None = None) -> str:
    """Return the spoken text carried by an event payload, or ``""`` if there is none.

    Engines differ in how they carry content: a bare string, or a dict under ``content``
    or ``text``. Normalising here keeps every adapter free of engine trivia, and an
    unrecognised shape yields nothing rather than speaking a ``repr`` of a dict at someone.

    ``speak_field`` is tried first, and is how a structured agent stays speakable. An agent with
    an ``output_schema`` emits an object; without a field to read, a voice pipeline would recite
    its JSON — braces, quotes, key names and all — which is both unusable and the kind of thing
    that only shows up once someone is listening.
    """
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        keys = (
            (speak_field, "content", "text", "delta")
            if speak_field
            else ("content", "text", "delta")
        )
        for key in keys:
            value = data.get(key)
            if isinstance(value, str):
                return value
    return ""
