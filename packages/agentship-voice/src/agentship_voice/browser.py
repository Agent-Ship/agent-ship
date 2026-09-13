"""Speak plain PCM, so a browser can be the microphone without a client library.

Pipecat ships serializers for protobuf and for telephony providers. Both are the right answer
when the other end is a Pipecat client or a phone network. Neither is the right answer for a
page of vanilla JavaScript: protobuf would mean shipping a schema and a decoder into Studio,
and the telephony formats encode someone else's call semantics.

What a browser already produces is 16-bit PCM, and what it can already play is 16-bit PCM. So
this serializer does nothing but move those bytes: audio out becomes the websocket's binary
payload, and a binary payload becomes an input audio frame. Everything else is ignored, which
is why Studio needs no build step and no dependency to talk to an agent.
"""

from __future__ import annotations

import json

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    InputAudioRawFrame,
    OutputAudioRawFrame,
    TranscriptionFrame,
    TTSTextFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.serializers.base_serializer import FrameSerializer

from .trace import LatencyTrace


class TurnTimingsFrame(Frame):
    """Carries a finished turn's :class:`~agentship_voice.trace.LatencyTrace` to the client.

    Our own frame type because Pipecat has no "here is where the time went" frame, and latency
    is a first-class part of a voice turn rather than a log line: a reply that is correct and
    slow is a different product from one that is correct and fast.
    """

    def __init__(self, trace: LatencyTrace) -> None:
        """Wrap the trace this turn produced."""
        super().__init__()
        self.trace = trace


class RawAudioSerializer(FrameSerializer):
    """Move raw PCM between a websocket and a pipeline, and nothing else."""

    def __init__(self, *, sample_rate: int = 16000) -> None:
        """Fix the rate both ends agree on.

        One rate for both directions on purpose: a browser can capture and play whatever it is
        told, so a second rate would buy nothing and add a resampling step to the one path
        where latency is heard.
        """
        super().__init__()
        self._sample_rate = sample_rate

    async def serialize(self, frame: Frame) -> str | bytes | None:
        """Return audio as bytes, a few events as JSON text, and ``None`` for the rest.

        Two payload kinds on one socket, told apart by type: a browser reads ``ArrayBuffer`` as
        sound and a string as an event. Audio alone is not enough for a UI — a caller staring
        at a silent circle cannot tell "not hearing you" from "thinking" from "about to
        speak", and a transcript they can read is how they find out the agent misheard rather
        than misunderstood.

        Only frames that change what a person should see are sent. Everything else still
        travels the pipeline and is simply not this transport's business.
        """
        if isinstance(frame, OutputAudioRawFrame):
            return frame.audio
        event = _event_for(frame)
        return json.dumps(event) if event else None

    async def deserialize(self, data: str | bytes) -> Frame | None:
        """Turn an inbound binary payload into an input audio frame.

        Text payloads are ignored rather than rejected: a browser may send a keepalive or a
        control message on the same socket, and a serializer that raised on one would drop the
        call over something harmless.
        """
        if isinstance(data, str):
            return None
        return InputAudioRawFrame(audio=data, sample_rate=self._sample_rate, num_channels=1)


def _event_for(frame: Frame) -> dict | None:
    """Return the UI event a frame represents, or ``None`` when it is not worth showing.

    Deliberately a small, closed set. A socket that forwarded every frame would make the
    browser responsible for knowing which of Pipecat's dozens matter, which is exactly the
    coupling the serializer exists to prevent.
    """
    if isinstance(frame, VADUserStartedSpeakingFrame):
        return {"type": "listening"}
    if isinstance(frame, VADUserStoppedSpeakingFrame):
        return {"type": "thinking"}
    if isinstance(frame, TranscriptionFrame):
        # What the agent HEARD. Shown because a wrong answer to a misheard question is an
        # entirely different problem from a wrong answer, and they look identical without it.
        return {"type": "heard", "text": frame.text}
    if isinstance(frame, TTSTextFrame):
        return {"type": "said", "text": frame.text}
    if isinstance(frame, BotStartedSpeakingFrame):
        return {"type": "speaking"}
    if isinstance(frame, BotStoppedSpeakingFrame):
        return {"type": "idle"}
    if isinstance(frame, TurnTimingsFrame):
        worst = frame.trace.biggest_contributor()
        return {
            "type": "timings",
            "trace": frame.trace.as_dict(),
            # Named here rather than computed in the browser: the rule for "which stage to
            # optimise next" belongs with the trace, not duplicated in every client.
            "biggest": {"stage": worst[0], "ms": worst[1]} if worst else None,
        }
    return None
