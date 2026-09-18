"""Framework adapters. Each imports its framework lazily, so importing this package is free.

An adapter is only meaningful with its extra installed (``agentship-voice[pipecat]`` or
``[livekit]``); the contract they implement lives in :mod:`agentship_voice.adapters.base`.

Adapters are discovered through the ``agentship.voice_frameworks`` entry-point group, the same
mechanism engines use — so a third party ships a class and one packaging line, and
``voice.framework: theirs`` works with no edit here. Our own two are registered the same way
rather than special-cased, because a plugin system its own author bypasses is one that quietly
stops working for everyone else.
"""

from agentship.errors import CapabilityError
from agentship.registry import Registry

from .base import VoiceAdapter

__all__ = ["VOICE_FRAMEWORKS", "VoiceAdapter", "get_adapter"]

#: Voice framework adapters by ``voice.framework`` name.
VOICE_FRAMEWORKS: Registry[type[VoiceAdapter]] = Registry(
    "agentship.voice_frameworks", label="voice framework"
)


def get_adapter(name: str) -> VoiceAdapter:
    """Return the adapter for ``name``, or raise naming the frameworks that exist.

    An unknown framework fails here rather than deep inside a session, and the message lists the
    real choices instead of leaving the author to guess the spelling.

    Loading is lazy by construction: an entry point is only imported when its name is asked for,
    so a stack with neither framework installed can still read this package's config and docs —
    and a broken third-party adapter cannot stop ours from loading.
    """
    adapter_class = VOICE_FRAMEWORKS.get(name)
    if adapter_class is None:
        known = ", ".join(VOICE_FRAMEWORKS.names()) or "none installed"
        raise CapabilityError(f"unknown voice framework {name!r} — available: {known}")
    return adapter_class()
