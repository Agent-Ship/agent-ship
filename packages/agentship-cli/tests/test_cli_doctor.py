"""Tests for ``agentship doctor`` — the offline config checker.

``doctor`` loads every agent YAML, resolves its engine from the registry, and
validates the spec against the engine's declared capabilities. These tests use
click's ``CliRunner`` and never touch the network: a green agent exits 0 with an
``OK`` line; an unknown-engine agent exits 1 with an actionable ``pip install``
hint and no traceback; a capability-mismatch agent exits 1 with a clear reason.
"""

from __future__ import annotations

from agentship_cli.main import main
from click.testing import CliRunner


def _write(path, text: str):
    """Write ``text`` to ``path`` and return the path (test scaffolding helper)."""
    path.write_text(text)
    return path


def test_doctor_green_agent_exits_zero_with_ok_line(tmp_path):
    """A valid echo agent reports OK and exits 0."""
    _write(tmp_path / "good.yaml", "name: good\nengine: echo\nprompt: hi\n")
    runner = CliRunner()
    result = runner.invoke(main, ["doctor", str(tmp_path / "good.yaml")])
    assert result.exit_code == 0, result.output
    assert "OK" in result.output
    assert "good" in result.output


def test_doctor_unknown_engine_gives_install_hint_no_traceback(tmp_path):
    """An unknown-but-known engine name yields an actionable pip hint, exit 1, no traceback."""
    _write(tmp_path / "bad.yaml", "name: bad\nengine: langgraph\nmodel: openai/gpt-4o-mini\n")
    # Simulate langgraph not being installed by hiding it from the registry.
    runner = CliRunner()
    from agentship.engines.base import ENGINES

    saved = ENGINES.get("langgraph")
    ENGINES._providers.pop("langgraph", None)
    ENGINES._discovered = True  # prevent re-discovery from re-adding it
    try:
        result = runner.invoke(main, ["doctor", str(tmp_path / "bad.yaml")])
    finally:
        if saved is not None:
            ENGINES._providers["langgraph"] = saved
        ENGINES._discovered = False
    assert result.exit_code == 1
    assert "langgraph" in result.output
    assert "pip install" in result.output
    assert "Traceback" not in result.output


def test_doctor_capability_mismatch_is_a_clear_reason(tmp_path):
    """An echo agent asking for structured output (unsupported) exits 1 with a clear reason."""
    _write(
        tmp_path / "mismatch.yaml",
        "name: mismatch\nengine: echo\noutput_schema: my.mod:Model\n",
    )
    runner = CliRunner()
    result = runner.invoke(main, ["doctor", str(tmp_path / "mismatch.yaml")])
    assert result.exit_code == 1
    assert "mismatch" in result.output
    assert "structured output" in result.output
    assert "Traceback" not in result.output


def test_doctor_autonomous_version_drift_is_flagged(tmp_path):
    """A template: autonomous agent is flagged when the pinned deepagents version drifts.

    The version guard reads the langgraph adapter's pinned version; simulating a
    drift (patching the pin) must make doctor exit 1 with an actionable pip hint —
    proving the guard is wired, not dormant. Non-vacuous: without the drift the same
    agent is green.
    """
    import pytest

    pytest.importorskip("deepagents")
    import agentship_langgraph.templates.autonomous_tpl as tpl

    _write(
        tmp_path / "da.yaml",
        "name: researcher\nengine: langgraph\ntemplate: autonomous\n"
        "model: openai/gpt-4o-mini\nprompt: be autonomous\n",
    )
    runner = CliRunner()

    # Green when the pin matches the installed version.
    ok_result = runner.invoke(main, ["doctor", str(tmp_path / "da.yaml")])
    assert ok_result.exit_code == 0, ok_result.output

    # Flagged when the pin drifts from what is installed.
    saved = tpl.PINNED_DEEPAGENTS_VERSION
    tpl.PINNED_DEEPAGENTS_VERSION = "9.9.9"
    try:
        drift_result = runner.invoke(main, ["doctor", str(tmp_path / "da.yaml")])
    finally:
        tpl.PINNED_DEEPAGENTS_VERSION = saved
    assert drift_result.exit_code == 1
    assert "deepagents" in drift_result.output
    assert "pip install" in drift_result.output
    assert "Traceback" not in drift_result.output


def test_doctor_mcp_version_out_of_range_is_flagged(tmp_path):
    """An agent declaring mcp: servers is flagged when the installed mcp SDK is out of range.

    Green when the installed mcp is within >=1.28,<2; simulating drift (patching the supported
    minimum above the installed version) must make doctor exit 1 with an actionable pip hint —
    proving the guard is wired, not dormant.
    """
    import pytest

    pytest.importorskip("mcp")
    import agentship_langgraph.mcp as mcp_mod

    _write(
        tmp_path / "m.yaml",
        "name: files\nengine: langgraph\nmodel: openai/gpt-4o-mini\n"
        "mcp:\n  fs:\n    transport: stdio\n    command: echo\n",
    )
    runner = CliRunner()

    ok_result = runner.invoke(main, ["doctor", str(tmp_path / "m.yaml")])
    assert ok_result.exit_code == 0, ok_result.output

    saved = mcp_mod._MCP_MIN
    mcp_mod._MCP_MIN = (99, 0)
    try:
        drift_result = runner.invoke(main, ["doctor", str(tmp_path / "m.yaml")])
    finally:
        mcp_mod._MCP_MIN = saved
    assert drift_result.exit_code == 1
    assert "mcp" in drift_result.output
    assert "pip install" in drift_result.output
    assert "Traceback" not in drift_result.output


def test_doctor_bad_yaml_is_a_clean_error(tmp_path):
    """Malformed YAML reports a clean Error/status line, exit 1, no traceback."""
    _write(tmp_path / "broken.yaml", "name: [unclosed\n")
    runner = CliRunner()
    result = runner.invoke(main, ["doctor", str(tmp_path / "broken.yaml")])
    assert result.exit_code == 1
    assert "Traceback" not in result.output


def test_doctor_agents_dir_scans_every_yaml(tmp_path):
    """--agents-dir validates every *.yaml; one bad agent fails the whole run."""
    agents = tmp_path / "agents"
    agents.mkdir()
    _write(agents / "ok.yaml", "name: ok\nengine: echo\n")
    _write(agents / "bad.yaml", "name: bad\nengine: echo\noutput_schema: m:M\n")
    runner = CliRunner()
    result = runner.invoke(main, ["doctor", "--agents-dir", str(agents)])
    assert result.exit_code == 1
    assert "ok" in result.output
    assert "bad" in result.output


def test_doctor_empty_dir_is_a_clean_error(tmp_path):
    """An agents dir with no YAML files reports a clean error, not a crash."""
    agents = tmp_path / "agents"
    agents.mkdir()
    runner = CliRunner()
    result = runner.invoke(main, ["doctor", "--agents-dir", str(agents)])
    assert result.exit_code == 1
    assert "Traceback" not in result.output
