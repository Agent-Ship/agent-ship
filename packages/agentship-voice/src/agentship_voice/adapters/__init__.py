"""Framework adapters. Each imports its framework lazily, so importing this package is free.

An adapter is only meaningful with its extra installed (``agentship-voice[pipecat]`` or
``[livekit]``); the contract they implement lives in :mod:`agentship_voice.adapters.base`.
"""

from .base import VoiceAdapter

__all__ = ["VoiceAdapter"]


#: Adapters by ``voice.framework`` name. Imported lazily by :func:`get_adapter` so that a
#: stack with neither framework installed can still read this package's config and docs.
_ADAPTERS = {
    "pipecat": "pipecat_adapter:PipecatAdapter",
    "livekit": "livekit_adapter:LiveKitAdapter",
}


def get_adapter(name: str) -> VoiceAdapter:
    """Return the adapter for ``name``, or raise naming the frameworks that exist.

    An unknown framework fails here rather than deep inside a session, and the message lists
    the real choices instead of leaving the author to guess the spelling.
    """
    from importlib import import_module

    if name not in _ADAPTERS:
        known = ", ".join(sorted(_ADAPTERS))
        raise ValueError(f"unknown voice framework {name!r} — available: {known}")
    module_name, class_name = _ADAPTERS[name].split(":")
    module = import_module(f".{module_name}", __package__)
    return getattr(module, class_name)()
