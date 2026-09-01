"""The single, in-code registry of AgentShip database migrations.

This module is the *one owning place* for all future DDL. The migration policy
(DESIGN.md §13.9 / §13.11, CLEAN-BUILD-PLAN.md "Migration policy") is:

- All schema creation is **idempotent, version-stamped, and gated** behind
  ``agentship db upgrade --allow-migrations``.
- **No phase ships its own ungated runner.** Every later phase that needs DDL
  (P02's checkpointer ``setup()``, P07's vault, P08's memory tables, P09's
  ``agent_tasks``) appends a :class:`Migration` to :data:`REGISTERED_MIGRATIONS`
  here instead of building a separate alembic/raw-SQL runner.
- A deployment on ``InMemorySaver`` + env API keys needs **zero DDL, zero approval**.
  DDL only matters once ``AGENT_SESSION_STORE_URI`` points at Postgres.

To register a migration, append a :class:`Migration` with a unique, sortable
``version`` (e.g. ``"0002_agent_tasks"``) and an ``apply`` callable that runs
**idempotent** DDL against the given database URL. The runner applies pending
migrations in ``version`` order; because each is idempotent, re-running
``db upgrade`` is safe.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class Migration:
    """One idempotent, version-stamped schema migration.

    Attributes:
        version: A unique, lexically sortable identifier (e.g.
            ``"0002_agent_tasks"``). Migrations are applied in ascending
            ``version`` order, so prefix with a zero-padded number.
        description: A short human-readable summary shown in the upgrade plan.
        apply: A callable taking the resolved ``database_url`` and performing
            **idempotent** DDL (e.g. ``CREATE TABLE IF NOT EXISTS ...``).
            Re-running an already-applied migration must be a safe no-op.
    """

    version: str
    description: str
    apply: Callable[[str], None]


#: The single owning list of migrations. **Empty by design** for Week-1 (zero
#: DDL). Later phases append their :class:`Migration` here — this is the only
#: sanctioned migration runner; no phase may ship its own.
def _create_checkpoint_tables(database_url: str) -> None:
    """Create the LangGraph checkpointer's tables, idempotently.

    A durable agent (``durability: checkpoint``) reads and writes these on every node. Until
    they exist, the first turn against a Postgres-backed deployment fails with
    ``relation "checkpoints" does not exist`` — which is what happens the moment
    ``AGENT_SESSION_STORE_URI`` is set and this migration has not been applied.

    The DDL itself is LangGraph's (``AsyncPostgresSaver.setup()``); registering it here is
    what puts it behind the single gated ``agentship db upgrade --allow-migrations`` entry
    point rather than having the engine create tables silently on boot.
    """
    import asyncio

    from agentship_langgraph.durability import open_checkpointer

    async def create() -> None:
        """Open the Postgres saver with ``setup=True``, which runs its schema DDL."""
        async with open_checkpointer(database_url, setup=True):
            pass

    asyncio.run(create())


#: Applied in ``version`` order by ``agentship db upgrade --allow-migrations``. Each entry is
#: idempotent, so re-running the command is always safe.
REGISTERED_MIGRATIONS: list[Migration] = [
    Migration(
        version="0001_langgraph_checkpoints",
        description="LangGraph checkpointer tables (needed by durability: checkpoint)",
        apply=_create_checkpoint_tables,
    ),
]
