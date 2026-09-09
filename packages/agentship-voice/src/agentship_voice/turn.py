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

from collections.abc import AsyncIterator

from agentship.context import Caller
from agentship.runtime import RunnableAgent

from .trace import LatencyTrace, _Stopwatch

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
        #: Everything the agent produced this turn — including text that was generated but
        #: never reached the speaker because the human cut in.
        self.generated: list[str] = []
        #: Only what an adapter has confirmed actually played. See :meth:`confirm_spoken`.
        self.spoken: list[str] = []
        #: Where this turn's time went. Filled stage by stage; see :mod:`agentship_voice.trace`.
        self.trace = LatencyTrace()

    async def say(self, text: str) -> AsyncIterator[str]:
        """Stream the agent's reply to ``text`` as chunks, in the order it produces them.

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
        watch = _Stopwatch()
        async for event in self.agent.stream(text, caller=self.caller, session_id=self.session_id):
            if event.type not in ("content", "token"):
                continue
            chunk = _text_of(event.data)
            if not chunk:
                continue
            if self.trace.llm_ttft_ms is None:
                self.trace.llm_ttft_ms = watch.ms()
            self.generated.append(chunk)
            yield chunk
        self.trace.llm_total_ms = watch.ms()

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


def _text_of(data: object) -> str:
    """Return the spoken text carried by an event payload, or ``""`` if there is none.

    Engines differ in how they carry content: a bare string, or a dict under ``content``
    or ``text``. Normalising here keeps every adapter free of engine trivia, and an
    unrecognised shape yields nothing rather than speaking a ``repr`` of a dict at someone.
    """
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        for key in ("content", "text", "delta"):
            value = data.get(key)
            if isinstance(value, str):
                return value
    return ""
