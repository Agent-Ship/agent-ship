"""Tests for ``agentship verify`` — the "verifiable agents" themed report.

These drive the command through click's ``CliRunner`` and stay fully offline: every
agent uses the model-free ``echo`` engine and no provider key is read, so the
conformance grid, the spec-validation gate, and the wired-contract sections all run
without the network. They pin the three promises the command exists to keep:

* a bare ``agentship verify`` runs the engine×capability grid and reports **0
  over-claims**, exiting 0;
* pointing ``--agents-dir`` at a valid ``echo`` spec passes spec-validation;
* a deliberately over-claiming spec (an ``echo`` agent that declares tools the engine
  cannot honour) makes ``verify`` exit non-zero and *names* the offending spec.
"""

from __future__ import annotations

from agentship_cli.main import main
from click.testing import CliRunner

_VALID_ECHO_SPEC = "name: good\nengine: echo\nprompt: hi\n"

#: An ``echo`` agent that declares ``tools:`` — a capability the echo engine does not
#: honour (``tool_calling=False``), so the capability gate rejects it. This is exactly
#: the over-claim ``verify`` must catch rather than pass silently.
_OVERCLAIMING_ECHO_SPEC = "name: overclaim\nengine: echo\nprompt: hi\ntools:\n  - calculator\n"


def test_verify_no_agents_dir_runs_grid_and_reports_zero_over_claims():
    """A bare ``verify`` runs the grid, reports 0 over-claims, and exits 0."""
    result = CliRunner().invoke(main, ["verify"])
    assert result.exit_code == 0, result.output
    assert "engine×capability grid" in result.output
    assert "0 over-claims" in result.output
    # The project-scoped sections have nothing to check without an agents dir.
    assert "spec validation" in result.output
    assert "SKIPPED" in result.output


def test_verify_valid_agents_dir_passes_spec_validation(tmp_path):
    """Pointing at a dir of valid echo specs passes spec-validation and exits 0."""
    (tmp_path / "good.yaml").write_text(_VALID_ECHO_SPEC)
    result = CliRunner().invoke(main, ["verify", "--agents-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "spec validation" in result.output
    assert "1/1" in result.output
    assert "0 over-claims" in result.output


def test_verify_over_claiming_spec_fails_and_is_named(tmp_path):
    """A spec declaring a capability the echo engine rejects makes verify exit non-zero.

    The invalid spec is named in the output (its filename plus the capability reason),
    and the run fails — the honesty rule: a broken contract is a real failure, never a
    fake green.
    """
    (tmp_path / "overclaim.yaml").write_text(_OVERCLAIMING_ECHO_SPEC)
    result = CliRunner().invoke(main, ["verify", "--agents-dir", str(tmp_path)])
    assert result.exit_code == 1, result.output
    assert "spec validation" in result.output
    # The offending spec is named, with the capability reason that rejected it.
    assert "overclaim.yaml" in result.output
    assert "tool" in result.output.lower()
    assert "FAILED" in result.output
