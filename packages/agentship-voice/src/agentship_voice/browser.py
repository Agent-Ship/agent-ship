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

from pipecat.frames.frames import Frame, InputAudioRawFrame, OutputAudioRawFrame
from pipecat.serializers.base_serializer import FrameSerializer


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
        """Return the audio bytes to send, or ``None`` for anything that is not audio.

        Returning ``None`` for non-audio frames is what keeps the socket a pure audio channel.
        Text, metrics and control frames still travel the pipeline; they are simply not this
        transport's business.
        """
        if isinstance(frame, OutputAudioRawFrame):
            return frame.audio
        return None

    async def deserialize(self, data: str | bytes) -> Frame | None:
        """Turn an inbound binary payload into an input audio frame.

        Text payloads are ignored rather than rejected: a browser may send a keepalive or a
        control message on the same socket, and a serializer that raised on one would drop the
        call over something harmless.
        """
        if isinstance(data, str):
            return None
        return InputAudioRawFrame(audio=data, sample_rate=self._sample_rate, num_channels=1)
