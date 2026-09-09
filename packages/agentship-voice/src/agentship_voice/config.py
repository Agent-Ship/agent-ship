"""The ``voice:`` block of an agent spec — which framework, which ears, which mouth.

Config names the *roles* (transport, VAD, STT, TTS) and leaves their implementations to the
chosen framework, because that is where they already exist and are maintained. This block is
our contract with the author; it is not a passthrough of any framework's own config, so a
spec written today keeps working when a framework renames a parameter.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

#: Voice frameworks with an adapter in this package. Both host the same agent; they differ in
#: where it sits (a Pipecat pipeline node vs. LiveKit's LLM slot) and in what they bundle.
Framework = Literal["pipecat", "livekit"]


class VoiceConfig(BaseModel):
    """How to run one agent over live audio.

    Every field has a working default except the provider keys, which come from the
    environment — a spec is committed to a repo and must never be where a key lives.
    """

    model_config = {"extra": "forbid"}

    framework: Framework = Field(
        default="pipecat", description="Which voice framework hosts the agent."
    )
    stt: str = Field(default="deepgram", description="Speech-to-text provider.")
    tts: str = Field(default="cartesia", description="Text-to-speech provider.")
    vad: str = Field(default="silero", description="Voice-activity detector for endpointing.")
    transport: str = Field(
        default="webrtc", description="How audio reaches the process (webrtc, websocket, sip)."
    )
    voice_id: str | None = Field(
        default=None, description="Provider voice id for TTS; None uses the provider default."
    )
    # Barge-in on by default: a voice agent a human cannot interrupt is worse than a text one,
    # because they must wait out a wrong answer instead of skimming past it.
    allow_interruptions: bool = Field(
        default=True, description="Let the human interrupt the agent mid-utterance."
    )
    # The mouth-to-ear target from DESIGN §8. Recorded so a run can be measured against the
    # number the design committed to, rather than against whatever it happens to achieve.
    latency_budget_ms: int = Field(
        default=850, ge=0, description="Target first-audio latency, for reporting."
    )
