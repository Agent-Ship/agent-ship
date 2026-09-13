"""``agentship voice serve`` — the launch path for running an agent over live audio.

Every check must happen BEFORE a transport is bound, because a voice session that dies on a
missing key mid-sentence fails where nobody can see it. So these assert what the command says
and what it exits with, using ``--dry-run`` — no port, no provider call, no key.
"""

from __future__ import annotations

from agentship_cli.main import main
from click.testing import CliRunner

VOICE_SPEC = "name: talker\nengine: echo\nprompt: hi\nvoice:\n  stt: openai\n  tts: openai\n"


def _spec(tmp_path, text: str = VOICE_SPEC, name: str = "voice.yaml"):
    """Write a spec file and return its path."""
    path = tmp_path / name
    path.write_text(text)
    return str(path)


def test_dry_run_reports_the_session_it_would_serve(tmp_path, monkeypatch) -> None:
    """A ready configuration is confirmed without binding anything or spending a call."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    result = CliRunner().invoke(main, ["voice", "serve", _spec(tmp_path), "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "talker" in result.output
    assert "pipecat" in result.output, "say which framework would host the agent"
    assert "openai → agent → openai" in result.output, "show the cascade, not just 'ok'"


def test_a_missing_key_is_reported_before_anything_binds(monkeypatch) -> None:
    """Missing credentials fail at launch with every problem listed, exit 1.

    Runs in an isolated filesystem on purpose. ``voice serve`` loads ``./.env`` like every
    other command, so a repository that has one on disk makes an unkeyed run look keyed — the
    test would pass for the wrong reason, and this is exactly the trap the demo suite fell into
    when a local ``.env`` quietly re-supplied a key that had been unset.
    """
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    runner = CliRunner()
    with runner.isolated_filesystem():
        with open("voice.yaml", "w") as handle:
            handle.write(VOICE_SPEC)
        result = runner.invoke(main, ["voice", "serve", "voice.yaml", "--dry-run"])

    assert result.exit_code == 1
    assert "voice cannot start" in result.output
    assert "OPENAI_API_KEY" in result.output


def test_an_agent_without_a_voice_block_says_so(tmp_path, monkeypatch) -> None:
    """A text-only agent is not a failure to diagnose — it is a missing block to add."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    path = _spec(tmp_path, "name: plain\nengine: echo\nprompt: hi\n", name="plain.yaml")
    result = CliRunner().invoke(main, ["voice", "serve", path, "--dry-run"])

    assert result.exit_code == 1
    assert "no `voice:` block" in result.output


def test_an_unknown_framework_names_the_real_choices(tmp_path, monkeypatch) -> None:
    """A typo in ``framework:`` is caught by the spec itself, before any adapter loads."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    path = _spec(
        tmp_path,
        "name: t\nengine: echo\nprompt: hi\nvoice:\n  framework: pipcat\n",
        name="bad.yaml",
    )
    result = CliRunner().invoke(main, ["voice", "serve", path, "--dry-run"])

    assert result.exit_code == 1
    assert "pipcat" in result.output


def test_voice_is_discoverable_from_the_top_level_help() -> None:
    """Someone who does not know the command exists must be able to find it."""
    result = CliRunner().invoke(main, ["--help"])
    assert "voice" in result.output
