"""Resolving provider names, and saying precisely what is missing before a session starts."""

from __future__ import annotations

import pytest
from agentship.errors import CapabilityError
from agentship_voice.config import VoiceConfig
from agentship_voice.factories import STT_PROVIDERS, TTS_PROVIDERS, make_stt, make_tts, preflight

pytest.importorskip("pipecat", reason="needs the [pipecat] extra")


def test_an_unknown_provider_names_the_real_choices() -> None:
    """A typo in ``stt:`` fails naming what exists, not with a bare KeyError."""
    with pytest.raises(CapabilityError) as raised:
        make_stt(VoiceConfig(stt="deepgrma"))
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
        make_tts(VoiceConfig(tts="openai"))
    assert "OPENAI_API_KEY" in str(raised.value), "name the variable that is not set"


def test_preflight_reports_every_problem_at_once(monkeypatch) -> None:
    """A first-time setup is one list to work through, not run-fix-run-fix."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("CARTESIA_API_KEY", raising=False)

    problems = preflight(VoiceConfig(stt="openai", tts="cartesia"))

    assert len(problems) >= 2, "both missing keys should be reported together"
    assert any("OPENAI_API_KEY" in p for p in problems)
    assert any("CARTESIA_API_KEY" in p or "pipecat-ai[cartesia]" in p for p in problems)


def test_preflight_is_quiet_when_everything_is_ready(monkeypatch) -> None:
    """Nothing missing means nothing reported — silence is the ready signal."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert preflight(VoiceConfig(stt="openai", tts="openai", vad="silero")) == []


def test_a_local_vad_needs_no_key(monkeypatch) -> None:
    """Silero runs in-process, so it is the one stage that costs nothing and needs no key."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    problems = preflight(VoiceConfig(stt="openai", tts="openai", vad="silero"))
    assert not any("vad" in p for p in problems), "a local model must not ask for credentials"


@pytest.mark.parametrize("table", [STT_PROVIDERS, TTS_PROVIDERS])
def test_every_registered_provider_declares_how_to_install_it(table) -> None:
    """A provider entry without its extra could only fail with an unactionable ImportError."""
    for name, provider in table.items():
        assert provider.extra, f"{name} must name the pipecat extra that installs it"
        assert provider.module.startswith("pipecat."), f"{name} must be a consumed service"
