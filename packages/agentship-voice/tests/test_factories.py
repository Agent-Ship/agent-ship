"""Resolving provider names, and saying precisely what is missing before a session starts."""

from __future__ import annotations

import pytest
from agentship.errors import CapabilityError
from agentship.spec import VoiceSpec
from agentship_voice.factories import (
    STT_PROVIDERS,
    TTS_PROVIDERS,
    make_stt,
    make_tts,
    make_vad,
    preflight,
)

pytest.importorskip("pipecat", reason="needs the [pipecat] extra")


def test_an_unknown_provider_names_the_real_choices() -> None:
    """A typo in ``stt:`` fails naming what exists, not with a bare KeyError."""
    with pytest.raises(CapabilityError) as raised:
        make_stt(VoiceSpec(stt="deepgrma"))
    message = str(raised.value)
    assert "deepgrma" in message
    assert "deepgram" in message, "the message must list the provider they meant"


def test_a_missing_key_is_reported_before_the_session_starts(monkeypatch) -> None:
    """A provider with no key fails at setup, not mid-sentence.

    Authenticating halfway through someone's first utterance fails in the least debuggable
    place there is — the human hears silence and the log blames the provider.
    """
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(CapabilityError) as raised:
        make_tts(VoiceSpec(tts="openai"))
    assert "OPENAI_API_KEY" in str(raised.value), "name the variable that is not set"


def test_preflight_reports_every_problem_at_once(monkeypatch) -> None:
    """A first-time setup is one list to work through, not run-fix-run-fix."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("CARTESIA_API_KEY", raising=False)

    problems = preflight(VoiceSpec(stt="openai", tts="cartesia"))

    assert len(problems) >= 2, "both missing keys should be reported together"
    assert any("OPENAI_API_KEY" in p for p in problems)
    assert any("CARTESIA_API_KEY" in p or "pipecat-ai[cartesia]" in p for p in problems)


def test_preflight_is_quiet_when_everything_is_ready(monkeypatch) -> None:
    """Nothing missing means nothing reported — silence is the ready signal."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert preflight(VoiceSpec(stt="openai", tts="openai", vad="silero")) == []


def test_a_local_vad_needs_no_key(monkeypatch) -> None:
    """Silero runs in-process, so it is the one stage that costs nothing and needs no key."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    problems = preflight(VoiceSpec(stt="openai", tts="openai", vad="silero"))
    assert not any("vad" in p for p in problems), "a local model must not ask for credentials"


@pytest.mark.parametrize("table", [STT_PROVIDERS, TTS_PROVIDERS])
def test_every_registered_provider_declares_how_to_install_it(table) -> None:
    """A provider entry without its extra could only fail with an unactionable ImportError."""
    for name, provider in table.items():
        assert provider.extra, f"{name} must name the pipecat extra that installs it"
        assert provider.module.startswith("pipecat."), f"{name} must be a consumed service"


def test_a_declared_language_reaches_the_recogniser(monkeypatch) -> None:
    """``language:`` pins recognition instead of letting it guess per utterance.

    Auto-detection gets short phrases wrong — spoken English "ChatGPT" came back as Urdu
    script, and the agent then answered in Urdu, which reads as the agent being broken rather
    than as the microphone being misheard.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    service = make_stt(VoiceSpec(stt="openai", language="en"))

    # Asserting the service actually KEPT it, not merely that we passed it: a value dropped on
    # the floor by the provider would leave auto-detection on and the bug intact.
    assert service._settings.language == "en", "the recogniser must carry the language given"


def test_an_unset_language_is_left_to_the_provider(monkeypatch) -> None:
    """No language means the provider decides — the old behaviour, still available on purpose.

    An agent that genuinely does not know which language it will hear is better served by
    auto-detection than by us guessing English for it.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert make_stt(VoiceSpec(stt="openai")) is not None


def test_an_unknown_language_code_is_passed_through(monkeypatch) -> None:
    """A code our enum does not know still reaches the provider rather than being rejected.

    Our list of languages will go out of date before the providers' does.
    """
    from agentship_voice.factories import _as_language

    assert _as_language("zz-XX") == "zz-XX"


def test_the_endpoint_silence_is_longer_than_a_pause_for_thought() -> None:
    """The default must not cut off someone who hesitates mid-sentence.

    Pipecat's own default is 200ms, which is shorter than an ordinary pause, so an agent using
    it talks over anyone who stops to think. This asserts the DEFAULT rather than the plumbing,
    because the plumbing was never the bug — the number was.
    """
    assert VoiceSpec().endpoint_silence_ms >= 500, "200ms interrupts normal speech"
    assert make_vad(VoiceSpec()).params.stop_secs >= 0.5


def test_the_endpoint_silence_is_the_author_s_to_set() -> None:
    """A terse exchange and a slow speaker want different numbers, so it is a spec field."""
    assert make_vad(VoiceSpec(endpoint_silence_ms=1200)).params.stop_secs == 1.2


def test_only_the_end_of_a_turn_is_made_patient() -> None:
    """``start_secs`` is left alone: making speech slower to RECOGNISE only adds latency.

    Every complaint is about being cut off, which is the silence at the end of a turn — so
    that is the only parameter overridden.
    """
    from pipecat.audio.vad.vad_analyzer import VADParams

    assert make_vad(VoiceSpec()).params.start_secs == VADParams().start_secs
