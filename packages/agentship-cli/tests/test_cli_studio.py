"""Tests for ``agentship studio`` — the loopback-only launcher for consumed studio UIs (P07 · §4.8).

The security guard is the headline (§13.8): the studio binds loopback only and refuses any other
host *before* generating a manifest or launching anything. These monkeypatch
:func:`run_studio_process` (the ``langgraph dev`` / ``adk web`` exec) so they assert the guard, the
generated manifest, and the launch command without spawning a process.
"""

from __future__ import annotations

import json

import agentship_cli.main as cli
from agentship_cli.main import main
from click.testing import CliRunner


def _write_langgraph_agent(tmp_path, name: str = "single"):
    """Write one valid LangGraph spec into an ``agents`` dir and return that dir."""
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / f"{name}.yaml").write_text(
        f"name: {name}\nengine: langgraph\nmodel: openai/gpt-4o-mini\nprompt: hi\n"
    )
    return agents


def _spy_launch(monkeypatch):
    """Replace ``run_studio_process`` with a spy that records the command instead of launching."""
    calls: list[dict] = []
    monkeypatch.setattr(
        cli, "run_studio_process", lambda cmd, env, *, cwd: calls.append({"cmd": cmd, "env": env})
    )
    return calls


def test_studio_generates_manifest_then_launches_langgraph(tmp_path, monkeypatch):
    """A valid agents dir writes a manifest and shells to ``langgraph dev`` on loopback."""
    agents = _write_langgraph_agent(tmp_path)
    calls = _spy_launch(monkeypatch)
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(main, ["studio", "--agents-dir", str(agents)])
    assert result.exit_code == 0, result.output

    manifest = json.loads((tmp_path / "langgraph.json").read_text())
    assert manifest["graphs"] == {"single": "./agentship_studio_graphs.py:single"}
    assert calls and calls[0]["cmd"][:2] == ["langgraph", "dev"]
    assert "--host" in calls[0]["cmd"] and "127.0.0.1" in calls[0]["cmd"]


def test_studio_refuses_non_loopback_host(tmp_path, monkeypatch):
    """A non-loopback host is refused (exit 1) before any manifest or launch."""
    agents = _write_langgraph_agent(tmp_path)
    calls = _spy_launch(monkeypatch)
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(main, ["studio", "--agents-dir", str(agents), "--host", "0.0.0.0"])
    assert result.exit_code == 1
    assert "loopback only" in result.output
    assert calls == []
    assert not (tmp_path / "langgraph.json").exists()


def test_studio_errors_when_no_langgraph_agent(tmp_path, monkeypatch):
    """An agents dir with no LangGraph agent fails clearly (exit 1) and never launches."""
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "noisy.yaml").write_text("name: noisy\nengine: echo\n")
    calls = _spy_launch(monkeypatch)
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(main, ["studio", "--agents-dir", str(agents)])
    assert result.exit_code == 1
    assert "no LangGraph agents" in result.output
    assert calls == []


def test_studio_adk_engine_shells_to_adk_web(tmp_path, monkeypatch):
    """``--engine adk`` shells to ADK web on loopback without generating a langgraph manifest."""
    agents = _write_langgraph_agent(tmp_path)
    calls = _spy_launch(monkeypatch)
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(main, ["studio", "--engine", "adk", "--agents-dir", str(agents)])
    assert result.exit_code == 0, result.output
    assert calls and calls[0]["cmd"][:2] == ["adk", "web"]
    assert not (tmp_path / "langgraph.json").exists()
