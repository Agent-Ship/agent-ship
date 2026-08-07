"""Tests for ``agentship db upgrade`` — the single, gated owner of all DDL.

These tests are fully offline (``CliRunner``, no database). They prove the
migration policy (DESIGN.md §13.9 / §13.11): the command is **plan-only by
default** and applies nothing, ``--allow-migrations`` is the only path that can
run a migration, and a registered migration runs *exactly once* only behind the
flag — the gate that makes ungated DDL impossible by construction.
"""

from __future__ import annotations

import agentship_cli.main as cli_main
import pytest
from agentship_cli.main import main
from agentship_cli.migrations import Migration
from click.testing import CliRunner


@pytest.fixture
def empty_registry(monkeypatch):
    """Force the registry empty (Week-1 state) regardless of later phases."""
    monkeypatch.setattr(cli_main, "REGISTERED_MIGRATIONS", [])
    return []


class _SpyMigration:
    """A fake migration whose ``apply`` records each call and its DSN.

    Used to prove the gate: the recorder stays empty unless
    ``--allow-migrations`` is passed, and records exactly one call when it is.
    """

    def __init__(self, version: str = "0001_test", description: str = "test table"):
        self.calls: list[str] = []
        self.migration = Migration(
            version=version, description=description, apply=self._apply
        )

    def _apply(self, database_url: str) -> None:
        self.calls.append(database_url)


def test_default_is_plan_only_and_applies_nothing(empty_registry):
    """`db upgrade` with no flag prints the plan and touches no database."""
    result = CliRunner().invoke(main, ["db", "upgrade"])
    assert result.exit_code == 0, result.output
    assert "No migrations registered — nothing to apply." in result.output
    assert "Plan-only" in result.output


def test_allow_migrations_empty_registry_is_a_no_op(empty_registry):
    """`--allow-migrations` over an empty registry succeeds as a no-op."""
    result = CliRunner().invoke(main, ["db", "upgrade", "--allow-migrations"])
    assert result.exit_code == 0, result.output
    assert "applied 0 migrations (0 pending)" in result.output


def test_allow_migrations_no_op_needs_no_database_url(empty_registry):
    """With zero migrations, no DSN is required — it just succeeds."""
    result = CliRunner().invoke(main, ["db", "upgrade", "--allow-migrations"])
    assert result.exit_code == 0, result.output


def test_plan_only_lists_a_registered_migration_but_never_runs_it(monkeypatch):
    """A registered migration is *listed* in the plan but its apply never runs."""
    spy = _SpyMigration()
    monkeypatch.setattr(cli_main, "REGISTERED_MIGRATIONS", [spy.migration])

    result = CliRunner().invoke(main, ["db", "upgrade"])
    assert result.exit_code == 0, result.output
    assert "0001_test" in result.output
    assert "would be applied" in result.output
    # The gate: apply must NOT have run without the flag.
    assert spy.calls == []


def test_allow_migrations_runs_a_registered_migration_exactly_once(monkeypatch):
    """`--allow-migrations` applies the registered migration exactly once."""
    spy = _SpyMigration()
    monkeypatch.setattr(cli_main, "REGISTERED_MIGRATIONS", [spy.migration])

    result = CliRunner().invoke(
        main,
        ["db", "upgrade", "--allow-migrations", "--database-url", "postgresql://x/y"],
    )
    assert result.exit_code == 0, result.output
    assert spy.calls == ["postgresql://x/y"]
    assert "applied 1 migrations (0 pending)" in result.output


def test_pending_migration_without_dsn_refuses_cleanly(monkeypatch):
    """Pending migration + no DSN → clean Error, non-zero exit, apply never runs."""
    spy = _SpyMigration()
    monkeypatch.setattr(cli_main, "REGISTERED_MIGRATIONS", [spy.migration])
    # Ensure no DSN leaks in from the ambient environment.
    for name in cli_main.DATABASE_URL_ENV_VARS:
        monkeypatch.delenv(name, raising=False)

    result = CliRunner().invoke(main, ["db", "upgrade", "--allow-migrations"])
    assert result.exit_code == 1
    assert "Error:" in result.output
    assert "no database URL" in result.output
    assert spy.calls == []  # refused before touching any DDL


def test_dsn_from_environment_is_used(monkeypatch):
    """The DSN falls back to $AGENTSHIP_DATABASE_URL when no flag is given."""
    spy = _SpyMigration()
    monkeypatch.setattr(cli_main, "REGISTERED_MIGRATIONS", [spy.migration])
    monkeypatch.setenv("AGENTSHIP_DATABASE_URL", "postgresql://env/db")

    result = CliRunner().invoke(main, ["db", "upgrade", "--allow-migrations"])
    assert result.exit_code == 0, result.output
    assert spy.calls == ["postgresql://env/db"]


def test_migrations_applied_in_version_order(monkeypatch):
    """Registered out of order, migrations still apply in ascending version order."""
    first = _SpyMigration(version="0001_a", description="first")
    second = _SpyMigration(version="0002_b", description="second")
    order: list[str] = []
    first.migration = Migration("0001_a", "first", lambda url: order.append("0001_a"))
    second.migration = Migration("0002_b", "second", lambda url: order.append("0002_b"))
    # Register in reverse to prove the runner sorts.
    monkeypatch.setattr(
        cli_main, "REGISTERED_MIGRATIONS", [second.migration, first.migration]
    )

    result = CliRunner().invoke(
        main,
        ["db", "upgrade", "--allow-migrations", "--database-url", "postgresql://x/y"],
    )
    assert result.exit_code == 0, result.output
    assert order == ["0001_a", "0002_b"]
