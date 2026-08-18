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
