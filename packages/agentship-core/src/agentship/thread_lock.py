"""The ONE single-owner-per-``thread_id`` advisory lock (design §13.6).

A durable run must have exactly one owner: if a second worker (or a replay) grabs a ``thread_id``
that is already being driven, it must be refused, not allowed to double-execute. :class:`ThreadLock`
enforces this with a **session-level** Postgres advisory lock held on its **own dedicated
connection** — deliberately *not* a transaction-level lock (``pg_advisory_xact_lock``) and *not* the
checkpointer's connection, because a xact lock spanning the whole run would either defeat
checkpoint-per-node durability or pin an idle-in-transaction connection. The lock is released, and
the dedicated connection closed, on exit.

Both P02 (this phase) and P09 (durable ``/tasks``) consume this single module — there is no second
lock implementation anywhere. A no-Postgres in-memory fallback for tests/dev is a separate task
(C8.4). See DESIGN §13.6.
"""

from __future__ import annotations

import hashlib
from types import TracebackType
from typing import TYPE_CHECKING

from .errors import ThreadBusyError

if TYPE_CHECKING:  # imported lazily at runtime so a bare install needn't have psycopg
    from psycopg import AsyncConnection


def advisory_key(tenant_id: str, thread_id: str) -> int:
    """Map ``(tenant_id, thread_id)`` to the signed 64-bit int ``pg_advisory_lock`` takes.

    The two fields are joined with a control-byte separator (so ``("a","bc")`` and ``("ab","c")``
    cannot collide) and hashed; the digest is read as a **signed** 64-bit integer because Postgres'
    single-argument advisory-lock functions take a ``bigint``. Deterministic: the same thread always
    maps to the same lock, across processes and workers.
    """
    payload = f"{tenant_id}\x1f{thread_id}".encode()
    digest = hashlib.blake2b(payload, digest_size=8).digest()
    return int.from_bytes(digest, "big", signed=True)


class ThreadLock:
    """Async context manager holding a session-level advisory lock for one ``(tenant, thread)``.

    ``async with ThreadLock(conninfo, tenant_id, thread_id):`` acquires the lock or raises
    :class:`~agentship.errors.ThreadBusyError` if another session holds it; the body runs as the
    sole owner; on exit the lock is released and the dedicated connection closed. The acquire is
    **non-blocking** (``pg_try_advisory_lock``) so a losing worker fails fast with an actionable
    409-mapped error rather than stalling behind the holder.
    """

    def __init__(self, conninfo: str, tenant_id: str, thread_id: str) -> None:
        """Bind the connection string and the ``(tenant, thread)`` whose lock this guards."""
        self._conninfo = conninfo
        self._key = advisory_key(tenant_id, thread_id)
        self._tenant_id = tenant_id
        self._thread_id = thread_id
        self._conn: AsyncConnection | None = None

    async def __aenter__(self) -> ThreadLock:
        """Open a dedicated connection and try to take the lock, or raise ``ThreadBusyError``."""
        from psycopg import AsyncConnection

        conn = await AsyncConnection.connect(self._conninfo, autocommit=True)
        try:
            async with conn.cursor() as cur:
                await cur.execute("SELECT pg_try_advisory_lock(%s)", (self._key,))
                row = await cur.fetchone()
            acquired = bool(row and row[0])
        except BaseException:
            await conn.close()
            raise
        if not acquired:
            await conn.close()
            raise ThreadBusyError(
                f"thread {self._thread_id!r} (tenant {self._tenant_id!r}) is already owned by "
                f"another worker — retry once the current owner finishes, or resume via /tasks"
            )
        self._conn = conn
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Release the advisory lock and close the dedicated connection (always)."""
        conn = self._conn
        self._conn = None
        if conn is None:
            return
        try:
            async with conn.cursor() as cur:
                await cur.execute("SELECT pg_advisory_unlock(%s)", (self._key,))
        finally:
            await conn.close()
