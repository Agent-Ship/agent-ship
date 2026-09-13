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
    Frame,
    InputAudioRawFrame,
    OutputAudioRawFrame,
    OutputTransportMessageFrame,
)
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
        """Return audio as bytes, a UI message as JSON text, and ``None`` for the rest.

        Two payload kinds on one socket, told apart by type: a browser reads ``ArrayBuffer`` as
        sound and a string as an event.

        Only two frame types are sent, because those are the only two the output transport
        actually offers a serializer — audio, and ``OutputTransportMessageFrame``. Anything
        else reaching here would be silently dropped, which is exactly what happened when this
        tried to serialise transcripts directly: the UI stayed empty while audio played
        perfectly, and nothing anywhere said why.
        """
        if isinstance(frame, OutputAudioRawFrame):
            return frame.audio
        if isinstance(frame, OutputTransportMessageFrame):
            return json.dumps(frame.message)
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
