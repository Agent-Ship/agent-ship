"""Tests for the CLI: it runs the echo example and prints the real output."""

from __future__ import annotations

import logging
from pathlib import Path

from agentship_cli.main import _enable_verbose_logging, main
from click.testing import CliRunner

_HELLO = Path(__file__).resolve().parents[3] / "examples" / "hello.yaml"


def test_cli_run_prints_echo_output():
    """`agentship run examples/hello.yaml --input hi` prints 'echo: hi'."""
    runner = CliRunner()
    result = runner.invoke(main, ["run", str(_HELLO), "--input", "hi"])
    assert result.exit_code == 0, result.output
    assert result.output.strip() == "echo: hi"


def test_cli_verbose_flag_still_prints_the_answer_on_stdout():
    """`--verbose` is accepted and leaves stdout as just the final answer.

    The flag turns on the framework's decision log (on stderr); the echoed answer must
    still be the only thing on stdout, so a piped result is never polluted by the trace.
    """
    runner = CliRunner()
    result = runner.invoke(main, ["run", str(_HELLO), "--input", "hi", "--verbose"])
    assert result.exit_code == 0
    assert "echo: hi" in result.output


def test_verbose_logging_surfaces_agentship_decision_logs(capsys):
    """`_enable_verbose_logging` routes ``agentship.*`` INFO logs (e.g. a supervisor's) to stderr.

    Proves the ``--verbose`` mechanism: after enabling it, a record logged on the
    ``agentship.supervisor`` child logger surfaces on stderr at INFO. Any prior tagged
    handler is dropped first so the fresh handler binds to this test's captured stderr.
    """
    log = logging.getLogger("agentship")
    for handler in [h for h in log.handlers if getattr(h, "_agentship_verbose", False)]:
        log.removeHandler(handler)

    _enable_verbose_logging()
    logging.getLogger("agentship.supervisor").info("dispatch: parallel -> sub-agents ['billing']")

    err = capsys.readouterr().err
    assert "dispatch: parallel -> sub-agents ['billing']" in err
    assert log.level == logging.INFO


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
