"""Where the tool-idempotency ledger lives: in memory, or in Postgres beside the checkpoints.

:func:`~agentship.primitives.idempotency.call_once` is only as good as the ledger it writes to. Its
``pending`` write-ahead exists for exactly one moment — the process dying between "about to fire the
side effect" and "it fired" — and an in-memory ledger dies in that same moment. After a restart the
dict is empty, the key is unseen, and the write fires a second time: the double charge the ledger
was built to prevent. So a deployment that keeps its checkpoints in Postgres keeps its ledger there
too, and :func:`open_ledger` makes that the same decision (``AGENT_SESSION_STORE_URI``) rather than
a second knob somebody forgets to turn.

The table is created by the gated ``agentship db upgrade --allow-migrations``, never on boot. A
ledger whose table is missing fails the tool call closed — the side effect does not fire — with a
message naming that command.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from ..errors import AgentShipError
from .idempotency import DictLedger, IdempotencyLedger, LedgerEntry

if TYPE_CHECKING:  # imported lazily at runtime so a bare install needn't have psycopg
    from psycopg import AsyncConnection

#: The durable ledger's table. Named in the idempotency module's docstrings since P02; one row per
#: side-effecting tool call, keyed by :func:`~agentship.primitives.idempotency.idem_key`.
LEDGER_TABLE = "tool_idempotency_keys"

#: Idempotent DDL, applied by the ``0002`` migration. ``updated_at`` records when a ``pending`` row
#: was settled, which is what an operator reads to find writes a crash left unresolved.
LEDGER_DDL = f"""
CREATE TABLE IF NOT EXISTS {LEDGER_TABLE} (
    key        text PRIMARY KEY,
    status     text NOT NULL CHECK (status IN ('pending', 'done')),
    result     jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
)
"""

_NOT_MIGRATED = (
    f"the tool idempotency ledger table {LEDGER_TABLE!r} does not exist — run "
    "`agentship db upgrade --allow-migrations` against AGENT_SESSION_STORE_URI. "
    "The side-effecting tool was NOT run."
)

#: The no-database ledger. Process-wide and shared, like the in-memory checkpointer, so an
#: in-process run then resume see the same entries; a restart forgets them, which is why a
#: deployment that needs crash safety sets ``AGENT_SESSION_STORE_URI``.
MEMORY_LEDGER = DictLedger()


def _jsonable(value: Any) -> Any:
    """Return ``value`` if it survives a JSON round-trip, else its ``str``.

    Tool results are strings in practice, but the ledger must not be the reason a write that already
    happened reports failure: recording ``done`` with a lossy result beats leaving the row
    ``pending`` and having the next resume treat a finished write as an unknown one.
    """
    try:
        json.dumps(value)
    except (TypeError, ValueError):
        return str(value)
    return value


class PostgresLedger:
    """An :class:`~agentship.primitives.idempotency.IdempotencyLedger` on a Postgres table.

    Holds one autocommit connection, opened on first use — most turns call no side-effecting tool
    and should not pay for a connection — and closed by :meth:`aclose`. Autocommit is the point:
    :meth:`record` returns only once the row is committed, so the ``pending`` write-ahead is on disk
    before the side effect fires. Parallel tool calls in one turn share the connection, which
    psycopg serialises.
    """

    def __init__(self, conninfo: str) -> None:
        """Bind the connection string; nothing is opened until the first read or write."""
        self._conninfo = conninfo
        self._conn: AsyncConnection | None = None
        self._connecting = asyncio.Lock()

    async def _connection(self) -> AsyncConnection:
        """The ledger's connection, opened once even if two tool calls arrive together."""
        async with self._connecting:
            if self._conn is None:
                from psycopg import AsyncConnection

                self._conn = await AsyncConnection.connect(self._conninfo, autocommit=True)
        return self._conn

    async def _execute(self, query: str, params: tuple) -> Any:
        """Run one statement, turning a missing table into an actionable error."""
        from psycopg.errors import UndefinedTable

        conn = await self._connection()
        try:
            return await conn.execute(query, params)
        except UndefinedTable as exc:
            raise AgentShipError(_NOT_MIGRATED) from exc

    async def lookup(self, key: str) -> LedgerEntry | None:
        """Return the committed entry for ``key``, or ``None`` if this call was never recorded."""
        cur = await self._execute(
            f"SELECT status, result FROM {LEDGER_TABLE} WHERE key = %s",
            (key,),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        return LedgerEntry(status=row[0], result=row[1])

    async def record(self, key: str, entry: LedgerEntry) -> None:
        """Upsert ``entry`` for ``key`` and return once it is committed."""
        from psycopg.types.json import Jsonb

        await self._execute(
            f"INSERT INTO {LEDGER_TABLE} (key, status, result) VALUES (%s, %s, %s) "
            "ON CONFLICT (key) DO UPDATE "
            "SET status = EXCLUDED.status, result = EXCLUDED.result, updated_at = now()",
            (key, entry.status, Jsonb(_jsonable(entry.result))),
        )

    async def aclose(self) -> None:
        """Close the connection if one was opened; safe to call twice."""
        conn, self._conn = self._conn, None
        if conn is not None:
            await conn.close()


@asynccontextmanager
async def open_ledger(conninfo: str | None) -> AsyncIterator[IdempotencyLedger]:
    """Yield the ledger for this environment, holding its connection for the ``with`` body.

    Mirrors the checkpointer's choice so the two can never disagree: blank ``conninfo`` →
    :data:`MEMORY_LEDGER`; a Postgres connection string → a :class:`PostgresLedger` closed on exit.
    """
    if not conninfo:
        yield MEMORY_LEDGER
        return
    ledger = PostgresLedger(conninfo)
    try:
        yield ledger
    finally:
        await ledger.aclose()


async def create_ledger_table(conninfo: str) -> None:
    """Apply :data:`LEDGER_DDL`. Idempotent; called only from the gated migration."""
    from psycopg import AsyncConnection

    async with await AsyncConnection.connect(conninfo, autocommit=True) as conn:
        await conn.execute(LEDGER_DDL)
