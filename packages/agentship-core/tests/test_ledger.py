"""The tool-idempotency ledger: async ledgers under ``call_once``, and the Postgres ledger itself.

The in-memory ledger dies with the process it protects, so it cannot stop a write re-firing after
a crash — the one job it has. These tests pin the durable replacement: a ``pending`` row is
committed (visible to another connection) *before* the side effect runs, entries survive into a
fresh ledger instance (a new process), and a missing table fails closed — the effect is not run.

The Postgres tests need ``AGENTSHIP_TEST_POSTGRES_URI`` and skip without it.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from agentship.errors import AgentShipError
from agentship.primitives.idempotency import LedgerEntry, call_once
from agentship.primitives.ledger import (
    LEDGER_TABLE,
    MEMORY_LEDGER,
    PostgresLedger,
    open_ledger,
)


class _Effect:
    """A ``call_once`` fn that counts its firings and records what ``verify`` was asked."""

    def __init__(self, result: str = "charged") -> None:
        self.fired = 0
        self.verified = 0
        self.result = result

    async def __call__(self) -> str:
        self.fired += 1
        return self.result

    async def verify(self) -> str:
        self.verified += 1
        return "not re-run"


class _AsyncLedger:
    """An async in-memory ledger, logging every operation — stands in for any database ledger."""

    def __init__(self) -> None:
        self.rows: dict[str, LedgerEntry] = {}
        self.log: list[tuple[str, str]] = []

    async def lookup(self, key: str) -> LedgerEntry | None:
        self.log.append(("lookup", key))
        return self.rows.get(key)

    async def record(self, key: str, entry: LedgerEntry) -> None:
        self.log.append((entry.status, key))
        self.rows[key] = entry


def _key() -> str:
    return f"k-{uuid.uuid4().hex}"


# ---- call_once over an ASYNC ledger ---------------------------------------------------------


async def test_call_once_awaits_an_async_ledger_and_writes_ahead():
    """An async ledger gets pending BEFORE the effect and done AFTER, in that order.

    Before this change ``call_once`` called ``ledger.record(...)`` without awaiting it, so an async
    ledger's write-ahead was a coroutine nobody ran — the row never existed.
    """
    ledger, effect, key = _AsyncLedger(), _Effect(), _key()

    result = await call_once(ledger, key, effect, idempotent=False)

    assert result == "charged" and effect.fired == 1
    assert ledger.log == [("lookup", key), ("pending", key), ("done", key)]
    assert ledger.rows[key] == LedgerEntry(status="done", result="charged")


async def test_call_once_replays_a_done_entry_from_an_async_ledger():
    """A recorded ``done`` is returned as-is; the effect does not fire."""
    ledger, effect, key = _AsyncLedger(), _Effect(), _key()
    ledger.rows[key] = LedgerEntry(status="done", result="first receipt")

    assert await call_once(ledger, key, effect, idempotent=False) == "first receipt"
    assert effect.fired == 0


async def test_call_once_verifies_a_pending_entry_instead_of_refiring():
    """A ``pending`` row means "may have happened" — verify, never fire blind."""
    ledger, effect, key = _AsyncLedger(), _Effect(), _key()
    ledger.rows[key] = LedgerEntry(status="pending")

    assert await call_once(ledger, key, effect, idempotent=False) == "not re-run"
    assert (effect.fired, effect.verified) == (0, 1)
    assert ledger.rows[key].status == "done"


async def test_a_failing_effect_leaves_the_row_pending():
    """If the effect raises, ``pending`` survives: the next attempt verifies, never refires."""
    ledger, key = _AsyncLedger(), _key()

    class _Boom(_Effect):
        async def __call__(self) -> str:
            raise RuntimeError("gateway timeout")

    with pytest.raises(RuntimeError):
        await call_once(ledger, key, _Boom(), idempotent=False)
    assert ledger.rows[key].status == "pending"


async def test_no_store_means_the_shared_memory_ledger():
    """Blank conninfo → the process-wide in-memory ledger, the same object every time."""
    async with open_ledger(None) as first, open_ledger("") as second:
        assert first is MEMORY_LEDGER and second is MEMORY_LEDGER


# ---- PostgresLedger against a real database ------------------------------------------------


async def test_postgres_ledger_round_trips_entries(postgres_uri):
    """Unseen → None; pending then done upserts one row; the result comes back as stored."""
    key = _key()
    async with open_ledger(postgres_uri) as ledger:
        assert isinstance(ledger, PostgresLedger)
        assert await ledger.lookup(key) is None
        await ledger.record(key, LedgerEntry(status="pending"))
        assert await ledger.lookup(key) == LedgerEntry(status="pending", result=None)
        await ledger.record(key, LedgerEntry(status="done", result={"refund_id": "rf_1"}))
        assert await ledger.lookup(key) == LedgerEntry(status="done", result={"refund_id": "rf_1"})


async def test_postgres_entries_outlive_the_ledger_that_wrote_them(postgres_uri):
    """A second ledger — a new process, as far as the database knows — sees the first one's rows.

    This is the property the in-memory ledger could not have, and the reason this module exists.
    """
    key = _key()
    async with open_ledger(postgres_uri) as writer:
        await call_once(writer, key, _Effect("receipt-1"), idempotent=False)

    later = _Effect()
    async with open_ledger(postgres_uri) as reader:
        assert await call_once(reader, key, later, idempotent=False) == "receipt-1"
    assert later.fired == 0, "the write fired again in a ledger that did not make it"


async def test_the_write_ahead_is_committed_before_the_effect_runs(postgres_uri):
    """While the effect is running, ANOTHER connection already sees the row as ``pending``.

    That is what makes the write-ahead worth anything: a process killed mid-effect leaves a
    committed ``pending`` behind, not an uncommitted transaction that rolls back with it.
    """
    from psycopg import AsyncConnection

    key = _key()
    seen_during_effect: list[str | None] = []

    class _Observed(_Effect):
        async def __call__(self) -> str:
            async with await AsyncConnection.connect(postgres_uri) as other:
                cur = await other.execute(
                    f"SELECT status FROM {LEDGER_TABLE} WHERE key = %s", (key,)
                )
                row = await cur.fetchone()
            seen_during_effect.append(row[0] if row else None)
            return await super().__call__()

    async with open_ledger(postgres_uri) as ledger:
        await call_once(ledger, key, _Observed(), idempotent=False)

    assert seen_during_effect == ["pending"]


async def test_a_result_json_cannot_hold_is_stored_as_text(postgres_uri):
    """A non-JSON result is recorded as its ``str`` — never left ``pending`` over a serializer."""
    key = _key()
    async with open_ledger(postgres_uri) as ledger:
        await ledger.record(key, LedgerEntry(status="done", result={1, 2}))
        entry = await ledger.lookup(key)
    assert entry.status == "done" and entry.result == "{1, 2}"


async def test_the_ledger_connects_only_when_used_and_only_once(postgres_uri):
    """Most turns call no side-effecting tool, so no connection until one does — then just one."""
    ledger = PostgresLedger(postgres_uri)
    assert ledger._conn is None
    await asyncio.gather(*(ledger.lookup(_key()) for _ in range(5)))
    first = ledger._conn
    assert first is not None
    await ledger.lookup(_key())
    assert ledger._conn is first
    await ledger.aclose()
    await ledger.aclose()  # idempotent
    assert ledger._conn is None


async def test_a_missing_table_fails_closed_and_names_the_fix(postgres_uri):
    """No ledger table → an actionable error, and the side effect is NOT run.

    Pointed at an empty schema via ``search_path`` so the real table is invisible.
    """
    from psycopg import AsyncConnection

    schema = f"empty_{uuid.uuid4().hex[:8]}"
    async with await AsyncConnection.connect(postgres_uri, autocommit=True) as admin:
        await admin.execute(f"CREATE SCHEMA {schema}")
    try:
        sep = "&" if "?" in postgres_uri else "?"
        unmigrated = f"{postgres_uri}{sep}options=-csearch_path%3D{schema}"
        effect = _Effect()
        async with open_ledger(unmigrated) as ledger:
            with pytest.raises(AgentShipError, match="agentship db upgrade --allow-migrations"):
                await call_once(ledger, _key(), effect, idempotent=False)
        assert effect.fired == 0, "the effect ran with no ledger to record it"
    finally:
        async with await AsyncConnection.connect(postgres_uri, autocommit=True) as admin:
            await admin.execute(f"DROP SCHEMA {schema} CASCADE")


def test_the_ledger_migration_is_idempotent(postgres_uri):
    """Re-running the migration is a no-op — ``db upgrade`` must always be safe to repeat."""
    from agentship_cli.migrations import REGISTERED_MIGRATIONS

    ledger_migration = next(m for m in REGISTERED_MIGRATIONS if "idempotency" in m.version)
    ledger_migration.apply(postgres_uri)
    ledger_migration.apply(postgres_uri)
