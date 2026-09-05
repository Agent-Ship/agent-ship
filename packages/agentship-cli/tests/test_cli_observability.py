"""`agentship run` honours a spec's ``observability`` block through the ``build_agent`` seam (#40).

The CLI builds the agent with :func:`~agentship.runtime.build_agent` and nothing else, so a YAML
that declares an ``observability`` block must run as a traced agent — with no observer wiring in the
CLI. These spy on the agent the CLI actually built: traced when the block is present, on the no-op
observer when it is absent, and either way the turn still prints its answer.
"""

from __future__ import annotations

from pathlib import Path

import agentship_cli.main as cli_main
from agentship.observability import NoOpObserver
from click.testing import CliRunner

#: The committed keyless example: echo engine + an observability block (see examples/README).
_EXAMPLE = Path(__file__).resolve().parents[3] / "examples" / "observability.yaml"

_TRACED_YAML = """\
name: traced
engine: echo
observability:
  provider: otel
  exporters: [console]
"""

_PLAIN_YAML = """\
name: plain
engine: echo
"""


def _run_and_capture_observer(tmp_path, monkeypatch, yaml_text: str):
    """Invoke ``agentship run`` on a spec, returning (result, the observer the CLI built)."""
    spec_path = tmp_path / "agent.yaml"
    spec_path.write_text(yaml_text)

    captured = {}
    real_build_agent = cli_main.build_agent

    def spy(source, *args, **kwargs):
        agent = real_build_agent(source, *args, **kwargs)
        captured["observer"] = agent.observer
        return agent

    monkeypatch.setattr(cli_main, "build_agent", spy)
    result = CliRunner().invoke(cli_main.main, ["run", str(spec_path), "--input", "hi"])
    return result, captured.get("observer")


def test_run_with_observability_block_builds_a_traced_agent(tmp_path, monkeypatch) -> None:
    """A spec with an ``observability`` block runs and the CLI's agent carries a real observer."""
    result, observer = _run_and_capture_observer(tmp_path, monkeypatch, _TRACED_YAML)
    assert result.exit_code == 0, result.output
    assert "echo: hi" in result.output
    assert not isinstance(observer, NoOpObserver)


def test_run_without_observability_block_is_traced(tmp_path, monkeypatch) -> None:
    """A spec with no block runs on the no-op observer — tracing is opt-in, never forced."""
    result, observer = _run_and_capture_observer(tmp_path, monkeypatch, _PLAIN_YAML)
    assert result.exit_code == 0, result.output
    assert not isinstance(observer, NoOpObserver), "tracing is on by default"


def test_committed_example_runs_traced_and_keyless(monkeypatch) -> None:
    """The documented ``examples/observability.yaml`` runs keyless (echo) and is traced.

    Backs the examples/README entry: no API key, no backend — the block alone turns tracing on.
    """
    captured = {}
    real_build_agent = cli_main.build_agent

    def spy(source, *args, **kwargs):
        agent = real_build_agent(source, *args, **kwargs)
        captured["observer"] = agent.observer
        return agent

    monkeypatch.setattr(cli_main, "build_agent", spy)
    result = CliRunner().invoke(cli_main.main, ["run", str(_EXAMPLE), "--input", "hi"])
    assert result.exit_code == 0, result.output
    assert "echo: hi" in result.output
    assert not isinstance(captured["observer"], NoOpObserver)
