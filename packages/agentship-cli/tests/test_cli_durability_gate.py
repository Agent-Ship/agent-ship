"""``doctor`` and ``serve`` refuse a durable agent that has nowhere durable to keep its runs.

``durability: checkpoint`` promises a run survives the process dying. With no
``AGENT_SESSION_STORE_URI`` the engine used to fall back to in-memory state without a word — the
promise broken silently, discovered only when a crashed run could not be resumed. These pin the
gate: fail before the port binds, name the fix, let a developer opt out explicitly, and leave
non-durable agents and ``verify`` alone.
"""

from __future__ import annotations

import json

import agentship_cli.main as cli
import pytest
from agentship_cli.main import ENV_ALLOW_IN_MEMORY_DURABILITY, main
from click.testing import CliRunner

_DURABLE = (
    "name: ledger\nengine: langgraph\nmodel: openai/gpt-4o-mini\nprompt: hi\n"
    "durability: checkpoint\n"
)
_PLAIN = "name: chat\nengine: langgraph\nmodel: openai/gpt-4o-mini\nprompt: hi\n"


@pytest.fixture(autouse=True)
def _no_store(monkeypatch):
    """Start every test with no store and no opt-out — and undo any the CLI sets in-process."""
    monkeypatch.delenv("AGENT_SESSION_STORE_URI", raising=False)
    monkeypatch.delenv(ENV_ALLOW_IN_MEMORY_DURABILITY, raising=False)


def _agents(tmp_path, *specs: str):
    agents = tmp_path / "agents"
    agents.mkdir()
    for i, text in enumerate(specs):
        (agents / f"a{i}.yaml").write_text(text)
    return agents


def _spy_run_server(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(cli, "run_server", lambda **kw: calls.append(kw))
    return calls


# ---- doctor ------------------------------------------------------------------------------------


def test_doctor_fails_a_durable_agent_with_no_store_and_names_the_fix(tmp_path):
    agents = _agents(tmp_path, _DURABLE)
    result = CliRunner().invoke(main, ["doctor", str(agents)])

    assert result.exit_code == 1, result.output
    assert "AGENT_SESSION_STORE_URI" in result.output
    assert "agentship db upgrade --allow-migrations" in result.output
    assert "--allow-in-memory-durability" in result.output


def test_doctor_passes_a_durable_agent_once_a_store_is_configured(tmp_path, monkeypatch):
    """Negative control for the test above: the SAME spec passes with a store — the store is why."""
    monkeypatch.setenv("AGENT_SESSION_STORE_URI", "postgresql://u:p@db:5432/agentship")
    agents = _agents(tmp_path, _DURABLE)
    result = CliRunner().invoke(main, ["doctor", str(agents)])
    assert result.exit_code == 0, result.output


def test_doctor_accepts_in_memory_durability_when_asked_to(tmp_path):
    agents = _agents(tmp_path, _DURABLE)
    result = CliRunner().invoke(main, ["doctor", str(agents), "--allow-in-memory-durability"])
    assert result.exit_code == 0, result.output


def test_the_opt_out_can_come_from_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_ALLOW_IN_MEMORY_DURABILITY, "1")
    agents = _agents(tmp_path, _DURABLE)
    assert CliRunner().invoke(main, ["doctor", str(agents)]).exit_code == 0


def test_a_non_durable_agent_needs_no_store(tmp_path):
    agents = _agents(tmp_path, _PLAIN)
    result = CliRunner().invoke(main, ["doctor", str(agents)])
    assert result.exit_code == 0, result.output


def test_verify_does_not_fail_a_correct_spec_over_a_missing_store(tmp_path):
    """``verify`` asks whether the SPEC is right. It is; the missing store is doctor's business."""
    from agentship_cli.main import _check_agent

    path = tmp_path / "ledger.yaml"
    path.write_text(_DURABLE)
    assert _check_agent(path, check_environment=False) is None
    assert "AGENT_SESSION_STORE_URI" in _check_agent(path, check_environment=True)


# ---- serve -------------------------------------------------------------------------------------


def test_serve_refuses_to_bind_a_durable_agent_with_no_store(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTSHIP_API_KEYS", json.dumps([]))
    calls = _spy_run_server(monkeypatch)
    agents = _agents(tmp_path, _PLAIN, _DURABLE)

    result = CliRunner().invoke(main, ["serve", "--agents-dir", str(agents)])

    assert result.exit_code == 1
    assert "AGENT_SESSION_STORE_URI" in result.output
    assert calls == [], "the server launched with a durability promise it cannot keep"


def test_serve_launches_with_a_store(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTSHIP_API_KEYS", json.dumps([]))
    monkeypatch.setenv("AGENT_SESSION_STORE_URI", "postgresql://u:p@db:5432/agentship")
    calls = _spy_run_server(monkeypatch)
    agents = _agents(tmp_path, _DURABLE)

    result = CliRunner().invoke(main, ["serve", "--agents-dir", str(agents)])

    assert result.exit_code == 0, result.output
    assert len(calls) == 1


def test_serve_opt_out_reaches_worker_processes(tmp_path, monkeypatch):
    """The flag is handed on through the environment, which ``--reload``/``--workers`` inherit."""
    import os

    monkeypatch.setenv("AGENTSHIP_API_KEYS", json.dumps([]))
    calls = _spy_run_server(monkeypatch)
    agents = _agents(tmp_path, _DURABLE)

    result = CliRunner().invoke(
        main, ["serve", "--agents-dir", str(agents), "--allow-in-memory-durability"]
    )

    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    assert os.environ.get(ENV_ALLOW_IN_MEMORY_DURABILITY) == "1"
