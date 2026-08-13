"""C8.3: the single-owner-per-thread advisory lock (`core/thread_lock.py`).

Two layers of proof:

* **Pure** — :func:`advisory_key` maps ``(tenant_id, thread_id)`` to a stable signed 64-bit int
  (the argument Postgres' ``pg_advisory_lock`` takes), deterministically and collision-avoidingly.
* **Integration (real Postgres)** — a second acquire of the same ``(tenant, thread)`` on a separate
  connection raises :class:`ThreadBusyError`; releasing re-enables it; distinct threads don't
  contend; and the lock lives on its own dedicated connection, independent of any other transaction.
  Gated on ``AGENT_SESSION_STORE_URI`` so it runs in CI / when a DB is present, and skips otherwise.
"""

from __future__ import annotations

import asyncio
import os

import pytest
from agentship.errors import ThreadBusyError
from agentship.thread_lock import (
    InMemoryThreadLock,
    ThreadLock,
    advisory_key,
    resolve_thread_lock,
)

_PG_URI = os.environ.get("AGENT_SESSION_STORE_URI")
requires_pg = pytest.mark.skipif(_PG_URI is None, reason="AGENT_SESSION_STORE_URI not set")


class TestAdvisoryKey:
    """The pure lock-key derivation — no database needed."""

    def test_is_a_signed_64bit_int(self) -> None:
        """The key fits Postgres' ``bigint`` advisory-lock argument (signed 64-bit)."""
        key = advisory_key("tenantA", "thread-1")
        assert isinstance(key, int)
        assert -(2**63) <= key < 2**63

    def test_is_deterministic(self) -> None:
        """Same inputs → same key, so the same thread always maps to the same lock."""
        assert advisory_key("t", "x") == advisory_key("t", "x")

    def test_distinguishes_tenant_and_thread(self) -> None:
        """Different tenant or thread → different key (no cross-tenant lock collision)."""
        base = advisory_key("t1", "x")
        assert advisory_key("t2", "x") != base
        assert advisory_key("t1", "y") != base

    def test_no_field_boundary_collision(self) -> None:
        """``("a","bc")`` and ``("ab","c")`` must not hash to the same lock."""
        assert advisory_key("a", "bc") != advisory_key("ab", "c")


@requires_pg
class TestThreadLockIntegration:
    """Real-Postgres behavior of the single-owner lock."""

    async def test_second_acquire_same_thread_raises_busy(self) -> None:
        """While one owner holds the lock, another acquire of the same thread → ThreadBusyError."""
        async with ThreadLock(_PG_URI, "t", "thread-A"):
            with pytest.raises(ThreadBusyError):
                async with ThreadLock(_PG_URI, "t", "thread-A"):
                    pass  # pragma: no cover - must not be reached

    async def test_release_re_enables_acquire(self) -> None:
        """After the holder exits (unlock + dedicated-conn close), the lock is grabbable again."""
        async with ThreadLock(_PG_URI, "t", "thread-B"):
            pass
        async with ThreadLock(_PG_URI, "t", "thread-B"):
            pass  # no ThreadBusyError → release worked

    async def test_distinct_threads_do_not_contend(self) -> None:
        """Two different threads can be held at once — the lock is per-thread, not global."""
        async with ThreadLock(_PG_URI, "t", "thread-C"):
            async with ThreadLock(_PG_URI, "t", "thread-D"):
                pass  # both held simultaneously, no error

    async def test_lock_is_independent_of_other_connections(self) -> None:
        """The lock is held on its own connection: a concurrent task on the same thread is refused.

        Runs the two acquires as concurrent tasks (separate dedicated connections) — exactly one
        proceeds, the other gets ThreadBusyError. This is the two-workers-one-thread guarantee.
        """
        started = asyncio.Event()
        outcomes: list[str] = []

        async def worker(hold: float) -> None:
            try:
                async with ThreadLock(_PG_URI, "t", "thread-E"):
                    outcomes.append("held")
                    started.set()
                    await asyncio.sleep(hold)
            except ThreadBusyError:
                outcomes.append("busy")

        first = asyncio.create_task(worker(0.3))
        await started.wait()  # ensure `first` holds the lock before `second` tries
        second = asyncio.create_task(worker(0.0))
        await asyncio.gather(first, second)

        assert sorted(outcomes) == ["busy", "held"]


class TestInMemoryThreadLock:
    """The no-Postgres fallback: same guarantees, process-local (dev / InMemorySaver path)."""

    async def test_second_acquire_same_thread_raises_busy(self) -> None:
        """A second acquire of a held thread raises ThreadBusyError, same as the PG lock."""
        async with InMemoryThreadLock("t", "A"):
            with pytest.raises(ThreadBusyError):
                async with InMemoryThreadLock("t", "A"):
                    pass  # pragma: no cover - must not be reached

    async def test_release_re_enables_acquire(self) -> None:
        """Exiting the context frees the thread for the next acquire."""
        async with InMemoryThreadLock("t", "B"):
            pass
        async with InMemoryThreadLock("t", "B"):
            pass

    async def test_distinct_threads_do_not_contend(self) -> None:
        """Different threads are independent locks."""
        async with InMemoryThreadLock("t", "C"):
            async with InMemoryThreadLock("t", "D"):
                pass

    async def test_release_even_on_exception(self) -> None:
        """The lock is freed when the body raises, not just on clean exit."""
        with pytest.raises(ValueError):
            async with InMemoryThreadLock("t", "E"):
                raise ValueError("boom")
        async with InMemoryThreadLock("t", "E"):
            pass  # would raise ThreadBusyError if the errored body had leaked the lock


class TestResolveThreadLock:
    """The factory picks the PG lock when a conninfo is given, else the in-memory fallback."""

    def test_no_conninfo_gives_in_memory(self) -> None:
        """With no connection string (no AGENT_SESSION_STORE_URI) → in-memory lock."""
        lock = resolve_thread_lock("t", "x", conninfo=None)
        assert isinstance(lock, InMemoryThreadLock)

    def test_conninfo_gives_postgres(self) -> None:
        """With a connection string → the real Postgres ThreadLock."""
        lock = resolve_thread_lock("t", "x", conninfo="host=/tmp dbname=whatever")
        assert isinstance(lock, ThreadLock)
