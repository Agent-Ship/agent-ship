"""Turn the names in ``voice:`` into LiveKit plugin objects, for ``framework: livekit``.

The same provider names as :mod:`agentship_voice.factories`, built from LiveKit's own plugins
instead of Pipecat's services. ``stt: deepgram`` must mean Deepgram whichever framework hosts
the agent, or ``voice.framework`` stops being a one-line switch.

Every plugin is **consumed**, exactly as on Pipecat: we map a name to a class, check that its SDK
is installed and its key is set, and pass the spec's settings under the argument name that
plugin actually uses. The plugins disagree about those names — Cartesia takes ``voice``,
ElevenLabs ``voice_id``, Rime ``speaker`` — so each is spelled out per provider rather than
guessed. A setting the chosen plugin has no argument for is logged and skipped, never passed
under a wrong name, where a plugin would either reject it or swallow it silently.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

from agentship.errors import CapabilityError
from agentship.spec import VoiceSpec

logger = logging.getLogger("agentship.voice")

#: What a LiveKit worker needs to register with a LiveKit server. LiveKit reads these names
#: itself; they are checked here so a missing one is reported with the rest, before a worker
#: starts and fails to connect.
CREDENTIAL_ENV_VARS = ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET")


@dataclass(frozen=True)
class _Plugin:
    """Where a LiveKit plugin class lives, and what it calls each setting."""

    plugin: str
    cls: str
    env_var: str | None
    model_arg: str | None = "model"
    language_arg: str | None = None
    voice_arg: str | None = None
    speed_arg: str | None = None
    hint_arg: str | None = None
    #: Fixed arguments that select a mode of the plugin, e.g. OpenAI's realtime transcription.
    fixed: dict = field(default_factory=dict)

    @property
    def module(self) -> str:
        """The import path of the plugin package."""
        return f"livekit.plugins.{self.plugin}"


STT_PLUGINS = {
    "deepgram": _Plugin("deepgram", "STT", "DEEPGRAM_API_KEY", language_arg="language"),
    "openai": _Plugin(
        "openai",
        "STT",
        "OPENAI_API_KEY",
        language_arg="language",
        hint_arg="prompt",
        fixed={"use_realtime": False},
    ),
    "openai-realtime": _Plugin(
        "openai",
        "STT",
        "OPENAI_API_KEY",
        language_arg="language",
        hint_arg="prompt",
        fixed={"use_realtime": True},
    ),
    "assemblyai": _Plugin("assemblyai", "STT", "ASSEMBLYAI_API_KEY", language_arg="language_code"),
    "elevenlabs": _Plugin("elevenlabs", "STT", "ELEVENLABS_API_KEY", language_arg="language_code"),
    "cartesia": _Plugin("cartesia", "STT", "CARTESIA_API_KEY", language_arg="language"),
    "gladia": _Plugin("gladia", "STT", "GLADIA_API_KEY"),
    "groq": _Plugin("groq", "STT", "GROQ_API_KEY", language_arg="language", hint_arg="prompt"),
    "speechmatics": _Plugin("speechmatics", "STT", "SPEECHMATICS_API_KEY", language_arg="language"),
}

TTS_PLUGINS = {
    "cartesia": _Plugin(
        "cartesia",
        "TTS",
        "CARTESIA_API_KEY",
        language_arg="language",
        voice_arg="voice",
        speed_arg="speed",
    ),
    "elevenlabs": _Plugin(
        "elevenlabs", "TTS", "ELEVENLABS_API_KEY", language_arg="language", voice_arg="voice_id"
    ),
    "openai": _Plugin("openai", "TTS", "OPENAI_API_KEY", voice_arg="voice", speed_arg="speed"),
    # Deepgram names its voices as models (``aura-2-andromeda-en``), so there is no separate
    # voice argument: select one with ``tts_model``.
    "deepgram": _Plugin("deepgram", "TTS", "DEEPGRAM_API_KEY", speed_arg="speed"),
    "rime": _Plugin("rime", "TTS", "RIME_API_KEY", voice_arg="speaker"),
    "lmnt": _Plugin("lmnt", "TTS", "LMNT_API_KEY", language_arg="language", voice_arg="voice"),
    "inworld": _Plugin(
        "inworld",
        "TTS",
        "INWORLD_API_KEY",
        language_arg="language",
        voice_arg="voice",
        speed_arg="speaking_rate",
    ),
    "neuphonic": _Plugin(
        "neuphonic",
        "TTS",
        "NEUPHONIC_API_KEY",
        model_arg=None,
        language_arg="lang_code",
        voice_arg="voice_id",
        speed_arg="speed",
    ),
}

#: Silero runs locally: no key, only the package.
VAD_PLUGINS = {"silero": _Plugin("silero", "VAD", None, model_arg=None)}


def _resolve(kind: str, name: str, table: dict[str, _Plugin]) -> _Plugin:
    """Return the plugin registered as ``name``, or raise listing the real choices."""
    try:
        return table[name]
    except KeyError:
        known = ", ".join(sorted(table))
        raise CapabilityError(
            f"unknown {kind} provider {name!r} on livekit — available: {known}"
        ) from None


def _load(plugin: _Plugin, kind: str, name: str):
    """Import a plugin's class, turning a missing SDK into the install line that fixes it."""
    from importlib import import_module

    try:
        module = import_module(plugin.module)
    except ImportError as exc:
        raise CapabilityError(
            f"{kind} provider {name!r} needs its LiveKit plugin — "
            f'pip install "livekit-agents[{plugin.plugin}]"'
        ) from exc
    return getattr(module, plugin.cls)


def _require_key(plugin: _Plugin, kind: str, name: str) -> str | None:
    """Return the plugin's API key, or raise naming the variable that is not set."""
    if plugin.env_var is None:
        return None
    key = os.environ.get(plugin.env_var)
    if not key:
        raise CapabilityError(f"{kind} provider {name!r} needs {plugin.env_var} to be set")
    return key


def _settings(plugin: _Plugin, kind: str, name: str, wanted: list[tuple[str, str, object]]):
    """Map ``(setting, spec field, value)`` onto ``plugin``'s argument names.

    A value the plugin has no argument for is logged with the spec field that set it and left
    out, rather than passed under a name the plugin does not take.
    """
    kwargs = dict(plugin.fixed)
    for setting, spec_field, value in wanted:
        if value is None:
            continue
        arg = getattr(plugin, f"{setting}_arg")
        if arg is None:
            logger.warning(
                "voice.%s is set but the livekit %s provider %r has no such setting; ignored",
                spec_field,
                kind,
                name,
            )
            continue
        kwargs[arg] = value
    return kwargs


def make_stt(config: VoiceSpec):
    """Build the LiveKit speech-to-text plugin named by ``config.stt``."""
    plugin = _resolve("stt", config.stt, STT_PLUGINS)
    service = _load(plugin, "stt", config.stt)
    kwargs = _settings(
        plugin,
        "stt",
        config.stt,
        [
            ("model", "stt_model", config.stt_model),
            # `language` always has a value, so a plugin without the setting stays quiet.
            ("language", "language", config.language if plugin.language_arg else None),
            ("hint", "stt_hint", config.stt_hint),
        ],
    )
    if key := _require_key(plugin, "stt", config.stt):
        kwargs["api_key"] = key
    return service(**kwargs)


def make_tts(config: VoiceSpec):
    """Build the LiveKit text-to-speech plugin named by ``config.tts``.

    ``voice_id`` is passed only when the spec sets one, so the plugin keeps its own default.
    """
    plugin = _resolve("tts", config.tts, TTS_PLUGINS)
    service = _load(plugin, "tts", config.tts)
    kwargs = _settings(
        plugin,
        "tts",
        config.tts,
        [
            ("model", "tts_model", config.tts_model),
            ("language", "language", config.language if plugin.language_arg else None),
            ("voice", "voice_id", config.voice_id),
            ("speed", "tts_speed", config.tts_speed),
        ],
    )
    if key := _require_key(plugin, "tts", config.tts):
        kwargs["api_key"] = key
    return service(**kwargs)


def make_vad(config: VoiceSpec):
    """Build the LiveKit voice-activity detector named by ``config.vad``.

    Left at the plugin's defaults. How long a silence ends a turn is set on the session as its
    endpointing delay (see :func:`turn_handling`), which LiveKit counts from the end of speech
    the VAD reports; stretching the VAD's own silence as well would count the pause twice.
    """
    plugin = _resolve("vad", config.vad, VAD_PLUGINS)
    return _load(plugin, "vad", config.vad).load()


def turn_handling(config: VoiceSpec) -> dict:
    """Map the ``voice:`` turn-taking settings onto LiveKit's ``turn_handling`` options."""
    return {
        "endpointing": {"min_delay": config.endpoint_silence_ms / 1000},
        "interruption": {
            "enabled": config.allow_interruptions,
            "min_words": config.interrupt_min_words,
        },
    }


def preflight(config: VoiceSpec, *, check_environment: bool = True) -> list[str]:
    """Return one message per problem with a LiveKit ``voice:`` block, or ``[]`` when ready.

    The LiveKit counterpart of :func:`agentship_voice.factories.preflight`, with the same split:
    an unknown provider name is always reported, a missing plugin, key or LiveKit credential
    only when ``check_environment`` is set.
    """
    problems: list[str] = []
    for kind, name, table in (
        ("stt", config.stt, STT_PLUGINS),
        ("tts", config.tts, TTS_PLUGINS),
        ("vad", config.vad, VAD_PLUGINS),
    ):
        try:
            plugin = _resolve(kind, name, table)
            if check_environment:
                _load(plugin, kind, name)
                _require_key(plugin, kind, name)
        except CapabilityError as exc:
            problems.append(str(exc))
    if check_environment:
        missing = [var for var in CREDENTIAL_ENV_VARS if not os.environ.get(var)]
        if missing:
            problems.append(
                f"livekit needs {', '.join(missing)} to be set — the worker registers with a "
                "LiveKit server (LiveKit Cloud, or `livekit-server --dev` locally)"
            )
    return problems
