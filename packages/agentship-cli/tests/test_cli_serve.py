"""Tests for ``agentship serve`` — the doctor-gated launch path for the runtime service.

``serve`` must validate every spec and build the auth provider *before* binding a socket,
so these monkeypatch :func:`run_server` (the uvicorn launch) to a spy and assert: a valid
agents dir reaches the launch with the resolved host/port; an invalid spec fails the gate
(exit 1) without ever launching; ``--reload`` with ``--workers>1`` is a usage error (exit 2);
and the app factory the server would import is itself constructible.
"""

from __future__ import annotations

import json

import agentship_cli.main as cli
from agentship_cli.main import main
from click.testing import CliRunner


def _write_agent(tmp_path, name: str = "support"):
    """Write one valid echo-engine spec into an ``agents`` dir and return that dir."""
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / f"{name}.yaml").write_text(f"name: {name}\nengine: echo\nprompt: hi\n")
    return agents


def _spy_run_server(monkeypatch):
    """Replace ``run_server`` with a spy that records its kwargs instead of binding a socket."""
    calls: list[dict] = []
    monkeypatch.setattr(cli, "run_server", lambda **kw: calls.append(kw))
    return calls


def test_serve_gates_then_launches_with_resolved_host_port(tmp_path, monkeypatch):
    """A valid agents dir passes the gate and launches uvicorn with the resolved host/port."""
    agents = _write_agent(tmp_path)
    monkeypatch.setenv("AGENTSHIP_API_KEYS", json.dumps([]))
    calls = _spy_run_server(monkeypatch)

    result = CliRunner().invoke(
        main, ["serve", "--agents-dir", str(agents), "--host", "0.0.0.0", "--port", "9100"]
    )
    assert result.exit_code == 0, result.output
    assert calls == [{"host": "0.0.0.0", "port": 9100, "reload": False, "workers": None}]
    assert "Serving 1 agent(s)" in result.output


def test_serve_doctor_gate_rejects_invalid_spec_without_launching(tmp_path, monkeypatch):
    """An invalid spec fails the doctor gate (exit 1) and never reaches the launch."""
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "broken.yaml").write_text("name: broken\nengine: no_such_engine\n")
    calls = _spy_run_server(monkeypatch)

    result = CliRunner().invoke(main, ["serve", "--agents-dir", str(agents)])
    assert result.exit_code == 1
    assert "doctor gate failed" in result.output
    assert calls == []


def test_serve_reload_and_workers_are_mutually_exclusive(tmp_path, monkeypatch):
    """``--reload`` with ``--workers>1`` is a usage error (exit 2), never launches."""
    agents = _write_agent(tmp_path)
    calls = _spy_run_server(monkeypatch)

    result = CliRunner().invoke(
        main, ["serve", "--agents-dir", str(agents), "--reload", "--workers", "4"]
    )
    assert result.exit_code == 2
    assert calls == []


def test_serve_unknown_auth_provider_fails_gate(tmp_path, monkeypatch):
    """An uninstalled auth provider fails fast at the gate (exit 1) before binding."""
    agents = _write_agent(tmp_path)
    calls = _spy_run_server(monkeypatch)

    result = CliRunner().invoke(
        main, ["serve", "--agents-dir", str(agents), "--auth", "no_such_provider"]
    )
    assert result.exit_code == 1
    assert "not installed" in result.output
    assert calls == []


def test_serve_factory_builds_an_app(tmp_path, monkeypatch):
    """The import-string factory the server uses builds a real app from the environment."""
    agents = _write_agent(tmp_path)
    monkeypatch.setenv("AGENTSHIP_AGENTS_DIR", str(agents))
    monkeypatch.setenv("AGENTSHIP_AUTH_PROVIDER", "api_key")
    monkeypatch.setenv("AGENTSHIP_API_KEYS", json.dumps([]))

    from agentship_service.serving import build_from_env

    app = build_from_env()
    assert app.state.agents.__len__() == 1


def test_serve_starts_when_one_agent_is_missing_a_key_this_machine_lacks(tmp_path, monkeypatch):
    """A key this machine lacks stops that agent doing that thing, not the whole service.

    Treating it as fatal meant a single voice agent with no `DEEPGRAM_API_KEY` took down the
    nine agents beside it that needed no keys at all, and the only way to get the service up
    was to move the spec out of the directory. Nobody should have to hide a file to run the
    rest of their agents.
    """
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    (tmp_path / "plain.yaml").write_text("name: plain\nengine: echo\n", encoding="utf-8")
    (tmp_path / "talker.yaml").write_text(
        "name: talker\nengine: echo\nvoice:\n  stt: deepgram\n  tts: openai\n", encoding="utf-8"
    )

    from agentship_cli.main import _agent_files, _check_agent

    specs = _agent_files(tmp_path)
    invalid = [p.name for p in specs if _check_agent(p, check_environment=False) is not None]
    assert invalid == [], "neither spec is invalid — only the environment is incomplete"

    not_ready = {p.name: _check_agent(p) for p in specs}
    assert not_ready["plain.yaml"] is None, "an agent needing nothing is ready"
    assert "DEEPGRAM_API_KEY" in (not_ready["talker.yaml"] or ""), (
        "and the one that is not ready must name what would fix it"
    )


def test_serve_still_refuses_to_start_on_a_genuinely_invalid_spec(tmp_path):
    """The line the exemption must not cross: a broken spec is broken on every machine.

    `stt: depgram` names a provider that does not exist anywhere, so serving around it would
    hide a deployment that was never going to work.
    """
    (tmp_path / "broken.yaml").write_text(
        "name: broken\nengine: echo\nvoice:\n  stt: depgram\n", encoding="utf-8"
    )

    from agentship_cli.main import _check_agent

    reason = _check_agent(tmp_path / "broken.yaml", check_environment=False)
    assert reason is not None and "depgram" in reason, f"got {reason!r}"
