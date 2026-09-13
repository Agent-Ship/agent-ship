"""Run the same AgentShip agent over a live audio stream.

The agent is the one the REST service runs — same spec, same memory, same tools, same tracing.
Only the hosting differs: a voice framework supplies the ears (transport, VAD, STT) and the
mouth (TTS), and this package supplies the agent in the shape that framework expects.

The public surface is small on purpose:

* :class:`~agentship_voice.turn.VoiceTurn` — text in, spoken text out. The whole vendor-free
  seam, and the only thing both frameworks could agree on.
* :class:`~agentship_voice.trace.LatencyTrace` — where a turn's time went, emitted every turn.
* :class:`~agentship.spec.VoiceSpec` — the ``voice:`` block. It lives in the **kernel**, beside
  ``ObservabilitySpec``, because the kernel owns every authoring surface and imports no vendor;
  the provider *names* in it are validated here, in :mod:`agentship_voice.factories`, which is
  the half that knows what it can actually build.

Adapters live in :mod:`agentship_voice.adapters` and each needs its own extra installed.
"""

from agentship.spec import VoiceSpec

from .trace import LatencyTrace
from .turn import VoiceTurn

__all__ = ["LatencyTrace", "VoiceSpec", "VoiceTurn"]
