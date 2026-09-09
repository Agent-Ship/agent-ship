"""Run the same AgentShip agent over a live audio stream.

The agent is the one the REST service runs — same spec, same memory, same tools, same
tracing. Only the hosting differs: a voice framework supplies ears (transport, VAD, STT) and
a mouth (TTS), and this package supplies the agent in the shape that framework expects.

The public surface is deliberately two things: :class:`~agentship_voice.turn.VoiceTurn` (text
in, spoken text out) and :class:`~agentship_voice.config.VoiceConfig` (the ``voice:`` block).
Adapters live under :mod:`agentship_voice.adapters` and each needs its own extra installed.
"""

from .config import VoiceConfig
from .turn import VoiceTurn

__all__ = ["VoiceConfig", "VoiceTurn"]
