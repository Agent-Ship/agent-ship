"""Turn the names in ``voice:`` into real provider objects — or say exactly what is missing.

A provider is named by a short string (``stt: deepgram``) rather than an import path, because
the spec is our contract with the author: it should survive a library reorganising its modules,
and it should be readable by someone who has never seen Pipecat.

Every provider here is **consumed**. Nothing in this module implements speech recognition or
synthesis; it maps a name to a class, checks the two things that actually go wrong — the SDK is
not installed, or the key is not set — and says which, naming the fix. That check exists because
the alternative is an ``ImportError`` from three frames inside a vendor package, or a 401 from a
provider halfway through someone's first conversation.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from agentship.errors import CapabilityError

from .config import VoiceConfig


@dataclass(frozen=True)
class _Provider:
    """Where a provider's class lives, and what it needs before it can be built."""

    module: str
    cls: str
    #: The environment variable holding its key, or ``None`` for a local model.
    env_var: str | None
    #: The Pipecat extra that installs its SDK, e.g. ``pipecat-ai[deepgram]``.
    extra: str


#: Speech-to-text providers, by the name used in ``voice.stt``.
STT_PROVIDERS = {
    "deepgram": _Provider(
        "pipecat.services.deepgram.stt", "DeepgramSTTService", "DEEPGRAM_API_KEY", "deepgram"
    ),
    "openai": _Provider(
        "pipecat.services.openai.stt", "OpenAISTTService", "OPENAI_API_KEY", "openai"
    ),
}

#: Text-to-speech providers, by the name used in ``voice.tts``.
TTS_PROVIDERS = {
    "cartesia": _Provider(
        "pipecat.services.cartesia.tts", "CartesiaTTSService", "CARTESIA_API_KEY", "cartesia"
    ),
    "openai": _Provider(
        "pipecat.services.openai.tts", "OpenAITTSService", "OPENAI_API_KEY", "openai"
    ),
}

#: Voice-activity detectors. Silero runs locally, so it needs no key — only the package.
VAD_PROVIDERS = {
    "silero": _Provider("pipecat.audio.vad.silero", "SileroVADAnalyzer", None, "silero"),
}


def _resolve(kind: str, name: str, table: dict[str, _Provider]) -> _Provider:
    """Return the provider registered as ``name``, or raise listing the real choices."""
    try:
        return table[name]
    except KeyError:
        known = ", ".join(sorted(table))
        raise CapabilityError(f"unknown {kind} provider {name!r} — available: {known}") from None


def _load(provider: _Provider, kind: str, name: str):
    """Import a provider's class, turning a missing SDK into an actionable message."""
    from importlib import import_module

    try:
        module = import_module(provider.module)
    except ImportError as exc:
        raise CapabilityError(
            f'{kind} provider {name!r} needs its SDK — pip install "pipecat-ai[{provider.extra}]"'
        ) from exc
    return getattr(module, provider.cls)


def _require_key(provider: _Provider, kind: str, name: str) -> str | None:
    """Return the provider's API key, or raise naming the variable that is not set.

    Checked before the session is assembled rather than at the first utterance: a voice agent
    that authenticates halfway through someone's first sentence fails in the least debuggable
    place there is.
    """
    if provider.env_var is None:
        return None
    key = os.environ.get(provider.env_var)
    if not key:
        raise CapabilityError(f"{kind} provider {name!r} needs {provider.env_var} to be set")
    return key


def make_stt(config: VoiceConfig):
    """Build the speech-to-text service named by ``config.stt``."""
    provider = _resolve("stt", config.stt, STT_PROVIDERS)
    service = _load(provider, "stt", config.stt)
    key = _require_key(provider, "stt", config.stt)
    return service(api_key=key) if key else service()


def make_tts(config: VoiceConfig):
    """Build the text-to-speech service named by ``config.tts``.

    ``voice_id`` is passed only when the spec sets one, so the provider keeps its own default
    rather than us inventing a voice on the author's behalf.
    """
    provider = _resolve("tts", config.tts, TTS_PROVIDERS)
    service = _load(provider, "tts", config.tts)
    key = _require_key(provider, "tts", config.tts)
    kwargs = {"api_key": key} if key else {}
    if config.voice_id:
        kwargs["voice_id"] = config.voice_id
    return service(**kwargs)


def make_vad(config: VoiceConfig):
    """Build the voice-activity detector named by ``config.vad``.

    The VAD is what decides the human has stopped talking. It runs locally, so it is the one
    stage in the cascade that costs nothing per turn and needs no key.
    """
    provider = _resolve("vad", config.vad, VAD_PROVIDERS)
    analyzer = _load(provider, "vad", config.vad)
    return analyzer()


def preflight(config: VoiceConfig) -> list[str]:
    """Return one message per missing dependency or key, or an empty list when ready.

    Collects every problem instead of raising on the first, so a first-time setup is one list
    to work through rather than run-fix-run-fix. This is what ``agentship doctor`` reports.
    """
    problems: list[str] = []
    for kind, name, table in (
        ("stt", config.stt, STT_PROVIDERS),
        ("tts", config.tts, TTS_PROVIDERS),
        ("vad", config.vad, VAD_PROVIDERS),
    ):
        try:
            provider = _resolve(kind, name, table)
            _load(provider, kind, name)
            _require_key(provider, kind, name)
        except CapabilityError as exc:
            problems.append(str(exc))
    return problems
