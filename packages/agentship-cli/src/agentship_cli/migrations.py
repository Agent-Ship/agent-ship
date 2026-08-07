"""The single, in-code registry of AgentShip database migrations.

This module is the *one owning place* for all future DDL. The migration policy
(DESIGN.md §13.9 / §13.11, CLEAN-BUILD-PLAN.md "Migration policy") is:

- All schema creation is **idempotent, version-stamped, and gated** behind
  ``agentship db upgrade --allow-migrations``.
- **No phase ships its own ungated runner.** Every later phase that needs DDL
  (P02's checkpointer ``setup()``, P07's vault, P08's memory tables, P09's
  ``agent_tasks``) appends a :class:`Migration` to :data:`REGISTERED_MIGRATIONS`
  here instead of building a separate alembic/raw-SQL runner.
- Week-1 runs on ``InMemorySaver`` + env API keys → **zero DDL, zero approval**.
  That is why :data:`REGISTERED_MIGRATIONS` is empty for now.

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
REGISTERED_MIGRATIONS: list[Migration] = []
