"""Tests for ``agentship init`` — scaffolding a single-tenant project.

``init`` creates a project directory with an ``agents/`` folder holding a starter
``assistant.yaml``, a ``.env.example`` and a ``README.md``. These tests prove the
scaffold is *valid, not just text*: the generated ``assistant.yaml`` loads via
``load_spec`` and BUILDS offline with a fake chat model injected — so a fresh
``init`` yields a runnable agent. They never touch the network.
"""

from __future__ import annotations

from pathlib import Path

import agentship_langgraph.models as models_module
import pytest
from agentship.runtime import build_agent
from agentship.spec import load_spec
from agentship_cli.main import main
from click.testing import CliRunner
from langchain_core.language_models.fake_chat_models import FakeListChatModel


@pytest.fixture
def fake_model(monkeypatch):
    """Inject a deterministic fake chat model in place of the real LiteLLM one.

    Patches :func:`agentship_langgraph.models.resolve_model` so ``build_agent``
    compiles the scaffolded langgraph agent over a ``FakeListChatModel`` — proving
    the wiring with zero network calls.
    """
    fake = FakeListChatModel(responses=["hello from the scaffold"])
    monkeypatch.setattr(models_module, "resolve_model", lambda *a, **k: fake)
    return fake


def test_init_creates_the_expected_tree(tmp_path):
    """`init DIR` creates agents/assistant.yaml, .env.example and README.md."""
    dest = tmp_path / "myapp"
    runner = CliRunner()
    result = runner.invoke(main, ["init", str(dest)])
    assert result.exit_code == 0, result.output
    assert (dest / "agents" / "assistant.yaml").is_file()
    assert (dest / ".env.example").is_file()
    assert (dest / "README.md").is_file()


def test_init_env_example_has_no_real_secret(tmp_path):
    """The scaffolded .env.example carries a commented placeholder, not a real key."""
    dest = tmp_path / "myapp"
    CliRunner().invoke(main, ["init", str(dest)])
    env = (dest / ".env.example").read_text()
    assert "OPENAI_API_KEY" in env
    assert "sk-" not in env  # no real-looking secret shipped


def test_init_readme_shows_the_run_command(tmp_path):
    """The README explains how to run the scaffolded agent."""
    dest = tmp_path / "myapp"
    CliRunner().invoke(main, ["init", str(dest)])
    readme = (dest / "README.md").read_text()
    assert "agentship run agents/assistant.yaml" in readme


def test_scaffolded_assistant_loads_and_builds(tmp_path, fake_model):
    """The generated assistant.yaml LOADS and BUILDS (offline) — proving it is valid."""
    dest = tmp_path / "myapp"
    CliRunner().invoke(main, ["init", str(dest)])
    spec_path = dest / "agents" / "assistant.yaml"

    spec = load_spec(spec_path)  # must not raise
    assert spec.engine == "langgraph"

    agent = build_agent(str(spec_path))  # must compile over the fake model
    assert agent.spec.name


def test_init_into_current_dir_by_default(tmp_path):
    """`init` with no DIR scaffolds into the current directory."""
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        result = runner.invoke(main, ["init"])
        assert result.exit_code == 0, result.output
        assert (Path("agents") / "assistant.yaml").is_file()


def test_init_does_not_overwrite_existing_files(tmp_path):
    """`init` refuses to clobber an existing scaffolded file, exiting non-zero."""
    dest = tmp_path / "myapp"
    runner = CliRunner()
    runner.invoke(main, ["init", str(dest)])
    # Mutate a file, re-run init, and assert it was not overwritten.
    (dest / "agents" / "assistant.yaml").write_text("name: mine\nengine: echo\n")
    result = runner.invoke(main, ["init", str(dest)])
    assert result.exit_code != 0
    assert (dest / "agents" / "assistant.yaml").read_text() == "name: mine\nengine: echo\n"
