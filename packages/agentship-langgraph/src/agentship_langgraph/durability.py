"""The durable-checkpointer substrate for the LangGraph engine (Phase 02 · C4).

One decision lives here: which LangGraph checkpointer a run uses. With no database configured
(dev, tests, the Week-1 slice) it is an in-memory saver; with ``AGENT_SESSION_STORE_URI`` set it
is a real ``AsyncPostgresSaver`` whose per-node checkpoints survive a crash so ``engine.resume``
can continue from the frontier. Because the Postgres saver owns a live connection pool that must
stay open for as long as the graph runs, this is exposed as an **async context manager**, not a
factory that returns a bare object.

``setup=True`` runs the saver's schema DDL. That is a migration, so per the project's "never migrate
silently" policy the caller opts in — it is invoked from ``agentship doctor`` / first-boot, never on
every process start. See phase 02 §C4 and DESIGN §6/§13.6.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from langgraph.checkpoint.memory import InMemorySaver

if TYPE_CHECKING:  # only for typing; the Postgres saver is imported lazily so a bare install works
    from langgraph.checkpoint.base import BaseCheckpointSaver


@asynccontextmanager
async def open_checkpointer(
    conninfo: str | None, *, setup: bool = False
) -> AsyncIterator[BaseCheckpointSaver]:
    """Yield the checkpointer for this environment, holding its resources for the ``with`` body.

    ``conninfo`` is the Postgres connection string (typically ``AGENT_SESSION_STORE_URI``). Blank
    or ``None`` → an :class:`InMemorySaver` (no cross-process durability, fine for dev/tests). A
    real connection string → an ``AsyncPostgresSaver`` bound to a live pool that is opened on enter
    and closed on exit. ``setup=True`` runs the saver's table DDL first (gated migration; Postgres
    only — the in-memory saver needs no schema).
    """
    if not conninfo:
        yield InMemorySaver()
        return

    # Imported lazily: psycopg + langgraph-checkpoint-postgres are the optional [postgres] extra,
    # so a bare install that never touches durability does not need them present.
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    async with AsyncPostgresSaver.from_conn_string(conninfo) as saver:
        if setup:
            await saver.setup()
        yield saver
