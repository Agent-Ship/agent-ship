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
from agentship.spec import VoiceSpec


@dataclass(frozen=True)
class _Provider:
    """Where a provider's class lives, and what it needs before it can be built."""

    module: str
    cls: str
    #: The environment variable holding its key, or ``None`` for a local model.
    env_var: str | None
    #: The Pipecat extra that installs its SDK, e.g. ``pipecat-ai[deepgram]``.
    extra: str
    #: What this provider calls its voice-selection argument. They disagree — OpenAI takes
    #: ``voice``, Cartesia takes ``voice_id`` — and a service that receives the wrong name
    #: swallows it in ``**kwargs`` and speaks in its default voice, so a setting the author
    #: wrote is silently dropped. ``None`` for providers that have no voice to select.
    voice_arg: str | None = None


#: Speech-to-text providers, by the name used in ``voice.stt``.
#:
#: Every one is CONSUMED — Pipecat implements the protocol, we map a name to a class and check
#: the two things that go wrong. Adding a provider is a line here, which is the point: an agent
#: should be able to change ears without changing anything else about itself.
#:
#: The environment variable is OUR convention, not the library's: Pipecat takes ``api_key`` as
#: an argument and reads nothing. Each name below is the one that vendor's own documentation
#: uses, so a key already exported for another tool is found without being renamed.
STT_PROVIDERS = {
    # Purpose-built for streaming speech; generally the most accurate on live audio.
    "deepgram": _Provider(
        "pipecat.services.deepgram.stt", "DeepgramSTTService", "DEEPGRAM_API_KEY", "deepgram"
    ),
    "openai": _Provider(
        "pipecat.services.openai.stt", "OpenAISTTService", "OPENAI_API_KEY", "openai"
    ),
    "assemblyai": _Provider(
        "pipecat.services.assemblyai.stt",
        "AssemblyAISTTService",
        "ASSEMBLYAI_API_KEY",
        "assemblyai",
    ),
    "elevenlabs": _Provider(
        "pipecat.services.elevenlabs.stt",
        "ElevenLabsSTTService",
        "ELEVENLABS_API_KEY",
        "elevenlabs",
    ),
    "cartesia": _Provider(
        "pipecat.services.cartesia.stt", "CartesiaSTTService", "CARTESIA_API_KEY", "cartesia"
    ),
    "gladia": _Provider(
        "pipecat.services.gladia.stt", "GladiaSTTService", "GLADIA_API_KEY", "gladia"
    ),
    # Whisper on Groq's hardware: cheap and fast, batch rather than truly streaming.
    "groq": _Provider("pipecat.services.groq.stt", "GroqSTTService", "GROQ_API_KEY", "groq"),
    "speechmatics": _Provider(
        "pipecat.services.speechmatics.stt",
        "SpeechmaticsSTTService",
        "SPEECHMATICS_API_KEY",
        "speechmatics",
    ),
}

#: Text-to-speech providers, by the name used in ``voice.tts``.
#:
#: ``voice_arg`` is what each one calls its voice-selection argument. They disagree, and a
#: service handed the wrong name swallows it in ``**kwargs`` and speaks in its default voice —
#: a setting the author wrote, silently dropped.
TTS_PROVIDERS = {
    # Low latency and a large voice library; the usual pairing with Deepgram for speed.
    "cartesia": _Provider(
        "pipecat.services.cartesia.tts",
        "CartesiaTTSService",
        "CARTESIA_API_KEY",
        "cartesia",
        voice_arg="voice_id",
    ),
    # The most natural voices most people recognise; slower than Cartesia.
    "elevenlabs": _Provider(
        "pipecat.services.elevenlabs.tts",
        "ElevenLabsTTSService",
        "ELEVENLABS_API_KEY",
        "elevenlabs",
        voice_arg="voice_id",
    ),
    "openai": _Provider(
        "pipecat.services.openai.tts",
        "OpenAITTSService",
        "OPENAI_API_KEY",
        "openai",
        voice_arg="voice",
    ),
    "deepgram": _Provider(
        "pipecat.services.deepgram.tts",
        "DeepgramTTSService",
        "DEEPGRAM_API_KEY",
        "deepgram",
        voice_arg="voice",
    ),
    "rime": _Provider(
        "pipecat.services.rime.tts", "RimeTTSService", "RIME_API_KEY", "rime", voice_arg="voice_id"
    ),
    "lmnt": _Provider(
        "pipecat.services.lmnt.tts", "LmntTTSService", "LMNT_API_KEY", "lmnt", voice_arg="voice_id"
    ),
    "inworld": _Provider(
        "pipecat.services.inworld.tts",
        "InworldTTSService",
        "INWORLD_API_KEY",
        "inworld",
        voice_arg="voice_id",
    ),
    "neuphonic": _Provider(
        "pipecat.services.neuphonic.tts",
        "NeuphonicTTSService",
        "NEUPHONIC_API_KEY",
        "neuphonic",
        voice_arg="voice_id",
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


def make_stt(config: VoiceSpec, *, sample_rate: int | None = None):
    """Build the speech-to-text service named by ``config.stt``.

    ``sample_rate`` pins the rate instead of taking whatever the transport negotiates. Leave it
    unset unless you have a reason: the pipeline's ``StartFrame`` carries the rate that was
    actually agreed, and pinning a different one means resampling.

    It does NOT make a service usable outside a pipeline. Pipecat stores the constructor value
    and only applies it in ``setup()``, which the pipeline calls — a service that was never set
    up reports rate 0, computes a zero-sized read and fails inside its HTTP client. Drive the
    pipeline, not the service.
    """
    provider = _resolve("stt", config.stt, STT_PROVIDERS)
    service = _load(provider, "stt", config.stt)
    key = _require_key(provider, "stt", config.stt)
    kwargs = {"api_key": key} if key else {}
    if sample_rate is not None:
        kwargs["sample_rate"] = sample_rate
    if config.language:
        # Pinning the language stops per-utterance auto-detection, which mistakes short English
        # phrases for other languages and makes the agent answer in one nobody spoke.
        kwargs["language"] = _as_language(config.language)
    if config.stt_model:
        kwargs["model"] = config.stt_model
    # Transcription is a reading task, not a writing one: sampling buys nothing and costs
    # invented words. Providers default this to their generative setting, which is why a
    # recogniser will confidently produce a plausible sentence over unclear audio.
    kwargs.setdefault("temperature", 0.0)
    if config.stt_hint:
        # Biases recognition toward words this agent actually deals in. A recogniser has no
        # idea what the conversation is about and will map an unfamiliar term onto a familiar
        # one; naming the vocabulary is the difference between "Pipecat" and "pipe cat".
        kwargs["prompt"] = config.stt_hint
    return service(**kwargs)


def _as_language(code: str):
    """Map a BCP-47 code onto the provider-neutral ``Language`` enum Pipecat services take.

    An unknown code is passed through as the plain string: a provider that understands it
    should get its chance, and one that does not will say so — better than us rejecting a
    language because our enum is out of date.
    """
    from pipecat.transcriptions.language import Language

    try:
        return Language(code)
    except ValueError:
        return getattr(Language, code.upper().replace("-", "_"), code)


def make_tts(config: VoiceSpec, *, sample_rate: int | None = None):
    """Build the text-to-speech service named by ``config.tts``.

    ``voice_id`` is passed only when the spec sets one, so the provider keeps its own default
    rather than us inventing a voice on the author's behalf. See :func:`make_stt` for when
    ``sample_rate`` needs passing.
    """
    provider = _resolve("tts", config.tts, TTS_PROVIDERS)
    service = _load(provider, "tts", config.tts)
    key = _require_key(provider, "tts", config.tts)
    kwargs = {"api_key": key} if key else {}
    if config.voice_id and provider.voice_arg:
        kwargs[provider.voice_arg] = config.voice_id
    if sample_rate is not None:
        kwargs["sample_rate"] = sample_rate
    if config.tts_model:
        kwargs["model"] = config.tts_model
    return service(**kwargs)


def make_vad(config: VoiceSpec):
    """Build the voice-activity detector named by ``config.vad``.

    The VAD is what decides the human has stopped talking. It runs locally, so it is the one
    stage in the cascade that costs nothing per turn and needs no key.

    ``endpoint_silence_ms`` from the spec sets how long a silence must last before the turn is
    taken as over. The library default is 200ms, which is shorter than a pause for thought, so
    an agent using it talks over anyone who hesitates mid-sentence.
    """
    provider = _resolve("vad", config.vad, VAD_PROVIDERS)
    analyzer = _load(provider, "vad", config.vad)
    from pipecat.audio.vad.vad_analyzer import VADParams

    # Only stop_secs is overridden. start_secs decides how quickly speech is RECOGNISED as
    # speech, and making that generous only adds latency; the complaint is always about being
    # cut off, which is the silence at the END of a turn.
    return analyzer(params=VADParams(stop_secs=config.endpoint_silence_ms / 1000))


def preflight(config: VoiceSpec) -> list[str]:
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
