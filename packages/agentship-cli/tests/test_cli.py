"""Tests for the CLI: it runs the echo example and prints the real output."""

from __future__ import annotations

from pathlib import Path

from agentship_cli.main import main
from click.testing import CliRunner

_HELLO = Path(__file__).resolve().parents[3] / "examples" / "hello.yaml"


def test_cli_run_prints_echo_output():
    """`agentship run examples/hello.yaml --input hi` prints 'echo: hi'."""
    runner = CliRunner()
    result = runner.invoke(main, ["run", str(_HELLO), "--input", "hi"])
    assert result.exit_code == 0, result.output
    assert result.output.strip() == "echo: hi"


def test_cli_stream_prints_echo_output():
    """The --stream path prints the same echoed text, terminated by a newline."""
    runner = CliRunner()
    result = runner.invoke(main, ["run", str(_HELLO), "--input", "hi", "--stream"])
    assert result.exit_code == 0, result.output
    assert result.output.strip() == "echo: hi"


def test_cli_unknown_engine_is_a_clean_error(tmp_path):
    """A spec on an unknown engine exits non-zero with a clean message, not a traceback."""
    bad = tmp_path / "bad.yaml"
    bad.write_text("name: x\nengine: nope\n")
    runner = CliRunner()
    result = runner.invoke(main, ["run", str(bad), "--input", "hi"])
    assert result.exit_code != 0
    assert "nope" in result.output
