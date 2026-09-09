"""Framework adapters. Each imports its framework lazily, so importing this package is free.

An adapter is only meaningful with its extra installed (``agentship-voice[pipecat]`` or
``[livekit]``); the contract they implement lives in :mod:`agentship_voice.adapters.base`.
"""

from .base import VoiceAdapter

__all__ = ["VoiceAdapter"]
