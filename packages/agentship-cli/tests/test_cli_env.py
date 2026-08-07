"""T8 proof (G4): the CLI loads a ``.env`` for real runs — and ONLY the CLI does.

These tests are fully offline and hermetic. They prove the anti-footgun property
that motivated the whole task: ``.env`` loading is scoped to the ``run`` command
so that importing ``agentship`` or running the suite never pulls a ``.env`` into
the environment (which is what once made tests fire stray paid provider calls).

The ``run`` command is driven with the keyless ``echo`` engine so no network is
touched: the assertions are about the environment side effect (a var appearing in
``os.environ``), not about any model call.
"""

from __future__ import annotations

import importlib
import os
from pathlib import Path

from agentship_cli.main import main
from click.testing import CliRunner

_HELLO = Path(__file__).resolve().parents[3] / "examples" / "hello.yaml"

#: A var name no real environment would set, used to observe ``.env`` loading.
_PROBE = "AGENTSHIP_TEST_VAR"


def _clear_probe() -> None:
    """Remove the probe var from ``os.environ`` if present (test isolation)."""
    os.environ.pop(_PROBE, None)


def test_run_loads_dotenv_from_cwd(tmp_path, monkeypatch):
    """`agentship run` loads a ``.env`` in the current directory into the environment.

    Writes a ``.env`` containing the probe var in a temp dir, points the process
    cwd there, runs the keyless echo agent, and asserts the var reached
    ``os.environ`` — i.e. a real run would find a provider key placed in ``.env``.
    """
    _clear_probe()
    (tmp_path / ".env").write_text(f"{_PROBE}=fromdotenv\n")
    monkeypatch.chdir(tmp_path)
    try:
        result = CliRunner().invoke(main, ["run", str(_HELLO), "--input", "hi"])
        assert result.exit_code == 0, result.output
        assert os.environ.get(_PROBE) == "fromdotenv"
    finally:
        _clear_probe()


def test_run_env_file_option_loads_named_file(tmp_path, monkeypatch):
    """`--env-file PATH` loads the named file even when it is not ``./.env``."""
    _clear_probe()
    custom = tmp_path / "custom.env"
    custom.write_text(f"{_PROBE}=fromcustom\n")
    # cwd has no .env, so only the explicit --env-file can supply the var.
    monkeypatch.chdir(tmp_path)
    try:
        result = CliRunner().invoke(
            main, ["run", str(_HELLO), "--input", "hi", "--env-file", str(custom)]
        )
        assert result.exit_code == 0, result.output
        assert os.environ.get(_PROBE) == "fromcustom"
    finally:
        _clear_probe()


def test_run_missing_env_file_is_a_clean_error(tmp_path):
    """A ``--env-file`` that does not exist yields a clean ``Error:`` and exit 1.

    No traceback should surface: this is user input at the CLI boundary, so it is
    reported like any other harness error.
    """
    missing = tmp_path / "nope.env"
    result = CliRunner().invoke(
        main, ["run", str(_HELLO), "--input", "hi", "--env-file", str(missing)]
    )
    assert result.exit_code == 1
    assert "Traceback (most recent call last)" not in result.output
    assert "Error:" in result.stderr
    assert str(missing) in result.stderr


def test_run_does_not_override_an_exported_var(tmp_path, monkeypatch):
    """An already-set env var wins over ``.env`` (``override=False`` semantics)."""
    (tmp_path / ".env").write_text(f"{_PROBE}=fromdotenv\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(_PROBE, "fromexport")
    result = CliRunner().invoke(main, ["run", str(_HELLO), "--input", "hi"])
    assert result.exit_code == 0, result.output
    # The exported value must survive: .env must not clobber it.
    assert os.environ.get(_PROBE) == "fromexport"


def test_importing_agentship_does_not_load_dotenv(tmp_path, monkeypatch):
    """Importing ``agentship`` / ``agentship_cli.main`` must NOT load a ``.env``.

    This is the critical anti-footgun guard: a ``.env`` sitting in the cwd must be
    invisible to a bare import (and therefore to pytest), so the suite never fires
    stray paid calls. Placing the probe in a cwd ``.env`` and re-importing the
    modules must leave the probe unset.
    """
    _clear_probe()
    (tmp_path / ".env").write_text(f"{_PROBE}=fromdotenv\n")
    monkeypatch.chdir(tmp_path)
    try:
        import agentship_cli.main

        import agentship

        importlib.reload(agentship)
        importlib.reload(agentship_cli.main)
        assert _PROBE not in os.environ
    finally:
        _clear_probe()
