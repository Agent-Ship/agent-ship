"""Tests for ``agentship new-agent`` — scaffolding one agent spec.

The scaffolded spec must be *loadable and buildable*, not just text: the default
langgraph agent loads via ``load_spec`` and BUILDS offline with a fake chat model
injected. The command also refuses to overwrite an existing spec.
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
    """Inject a deterministic fake chat model so building needs no network."""
    fake = FakeListChatModel(responses=["ok"])
    monkeypatch.setattr(models_module, "resolve_model", lambda *a, **k: fake)
    return fake


def test_new_agent_writes_a_loadable_buildable_spec(tmp_path, fake_model):
    """`new-agent NAME` writes agents/NAME.yaml that loads and builds offline."""
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        result = runner.invoke(main, ["new-agent", "researcher"])
        assert result.exit_code == 0, result.output
        spec_path = Path("agents") / "researcher.yaml"
        assert spec_path.is_file()

        spec = load_spec(spec_path)
        assert spec.name == "researcher"
        assert spec.engine == "langgraph"

        agent = build_agent(str(spec_path))  # must compile over the fake model
        assert agent.spec.name == "researcher"


def test_new_agent_honors_agents_dir(tmp_path):
    """`--agents-dir` controls where the spec is written."""
    dest = tmp_path / "specs"
    runner = CliRunner()
    result = runner.invoke(main, ["new-agent", "helper", "--agents-dir", str(dest)])
    assert result.exit_code == 0, result.output
    assert (dest / "helper.yaml").is_file()


def test_new_agent_refuses_to_overwrite(tmp_path):
    """A second `new-agent` for the same name exits non-zero and preserves the file."""
    dest = tmp_path / "agents"
    runner = CliRunner()
    runner.invoke(main, ["new-agent", "dup", "--agents-dir", str(dest)])
    (dest / "dup.yaml").write_text("name: mine\nengine: echo\n")
    result = runner.invoke(main, ["new-agent", "dup", "--agents-dir", str(dest)])
    assert result.exit_code != 0
    assert (dest / "dup.yaml").read_text() == "name: mine\nengine: echo\n"
