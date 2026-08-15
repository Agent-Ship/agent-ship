"""Tests for ``agentship new-agent --template single|graph|autonomous`` (T6).

The ``--template`` option scaffolds the *right file set* for each template and —
critically — the scaffolded ``single`` and ``graph`` specs are not just text: they
LOAD (``load_spec``) and BUILD (``build_agent``, offline over a fake model),
proving the scaffold is buildable, not a stringly-typed stub that drifts from the
spec schema. An unknown ``--template`` is a clean error, and every template refuses
to overwrite existing files without ``--force``.
"""

from __future__ import annotations

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


def test_default_template_is_single(tmp_path, fake_model):
    """No ``--template`` defaults to ``single``: one YAML with ``template: single``."""
    dest = tmp_path / "agents"
    runner = CliRunner()
    result = runner.invoke(main, ["new-agent", "greeter", "--agents-dir", str(dest)])
    assert result.exit_code == 0, result.output

    yaml_path = dest / "greeter.yaml"
    assert yaml_path.is_file()
    # No python scaffolding for the single template.
    assert not (dest / "greeter").exists()

    spec = load_spec(yaml_path)
    assert spec.template == "single"
    assert spec.engine == "langgraph"
    # LOADS and BUILDS: the single scaffold is a runnable spec, not just text.
    agent = build_agent(str(yaml_path))
    assert agent.spec.name == "greeter"


def test_single_template_explicit(tmp_path, fake_model):
    """``--template single`` writes only ``NAME.yaml`` (no ``agent.py``)."""
    dest = tmp_path / "agents"
    runner = CliRunner()
    result = runner.invoke(
        main, ["new-agent", "solo", "--template", "single", "--agents-dir", str(dest)]
    )
    assert result.exit_code == 0, result.output
    assert (dest / "solo.yaml").is_file()
    assert not (dest / "solo").exists()
    spec = load_spec(dest / "solo.yaml")
    assert spec.template == "single"


def test_graph_template_writes_yaml_and_agent_py(tmp_path, fake_model):
    """``--template graph`` writes ``NAME.yaml`` + ``NAME/agent.py`` that build offline.

    The YAML points ``code:`` at the scaffolded ``agent.py``; loading + building the
    spec must resolve that code, instantiate the ``LangGraphAgent`` subclass, and
    compile its ``build_graph`` over the fake model — proving the scaffold is a
    real, buildable custom agent (not a text stub).
    """
    dest = tmp_path / "agents"
    runner = CliRunner()
    result = runner.invoke(
        main, ["new-agent", "triager", "--template", "graph", "--agents-dir", str(dest)]
    )
    assert result.exit_code == 0, result.output

    yaml_path = dest / "triager.yaml"
    agent_py = dest / "triager" / "agent.py"
    assert yaml_path.is_file()
    assert agent_py.is_file()
    # The scaffold carries author TODO markers to fill in.
    assert "TODO(author)" in agent_py.read_text()

    spec = load_spec(yaml_path)
    assert spec.name == "triager"
    assert spec.engine == "langgraph"
    # LOADS and BUILDS: resolves code -> subclass -> build_graph over the fake model.
    agent = build_agent(str(yaml_path))
    assert agent.spec.name == "triager"


def test_autonomous_template_writes_yaml(tmp_path):
    """``--template autonomous`` writes ``NAME.yaml`` with ``template: autonomous``.

    Not built here: the autonomous template needs an optional extra + a tool-calling
    model (deferred to P03), so the DoD for this template is a coherent, loadable spec.
    """
    dest = tmp_path / "agents"
    runner = CliRunner()
    result = runner.invoke(
        main, ["new-agent", "autob", "--template", "autonomous", "--agents-dir", str(dest)]
    )
    assert result.exit_code == 0, result.output
    yaml_path = dest / "autob.yaml"
    assert yaml_path.is_file()
    assert not (dest / "autob").exists()
    spec = load_spec(yaml_path)
    assert spec.template == "autonomous"
    assert spec.engine == "langgraph"


def test_unknown_template_is_a_clean_error(tmp_path):
    """An unknown ``--template`` exits non-zero with a clean message, writing nothing."""
    dest = tmp_path / "agents"
    runner = CliRunner()
    result = runner.invoke(
        main, ["new-agent", "nope", "--template", "wizard", "--agents-dir", str(dest)]
    )
    assert result.exit_code != 0
    assert not (dest / "nope.yaml").exists()


def test_invalid_name_is_rejected(tmp_path):
    """A NAME that is not a valid spec identifier is refused before any file is written."""
    dest = tmp_path / "agents"
    runner = CliRunner()
    result = runner.invoke(
        main, ["new-agent", "Bad Name!", "--agents-dir", str(dest)]
    )
    assert result.exit_code != 0
    assert not dest.exists() or not any(dest.iterdir())


def test_graph_refuses_overwrite_without_force(tmp_path, fake_model):
    """A second ``graph`` scaffold refuses to clobber an existing agent.py."""
    dest = tmp_path / "agents"
    runner = CliRunner()
    first = runner.invoke(
        main, ["new-agent", "dupg", "--template", "graph", "--agents-dir", str(dest)]
    )
    assert first.exit_code == 0, first.output
    (dest / "dupg" / "agent.py").write_text("# mine, keep it\n")
    second = runner.invoke(
        main, ["new-agent", "dupg", "--template", "graph", "--agents-dir", str(dest)]
    )
    assert second.exit_code != 0
    assert (dest / "dupg" / "agent.py").read_text() == "# mine, keep it\n"


def test_force_overwrites(tmp_path, fake_model):
    """``--force`` allows re-scaffolding over existing files."""
    dest = tmp_path / "agents"
    runner = CliRunner()
    runner.invoke(main, ["new-agent", "over", "--agents-dir", str(dest)])
    (dest / "over.yaml").write_text("name: stale\nengine: echo\n")
    result = runner.invoke(
        main, ["new-agent", "over", "--force", "--agents-dir", str(dest)]
    )
    assert result.exit_code == 0, result.output
    spec = load_spec(dest / "over.yaml")
    assert spec.name == "over"
    assert spec.template == "single"
