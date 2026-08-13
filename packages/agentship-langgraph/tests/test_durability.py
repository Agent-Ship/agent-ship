"""C4.1: the durable-checkpointer substrate (`agentship_langgraph/durability.py`).

``open_checkpointer(conninfo)`` is the one place that decides *which* LangGraph checkpointer a run
uses: an in-memory saver when there is no database (dev / tests / Week-1), or a real
``AsyncPostgresSaver`` when ``AGENT_SESSION_STORE_URI`` is set. It is an async context manager
because the Postgres saver owns a live connection pool that must stay open for the graph's life.
``setup=True`` runs the saver's table DDL — gated (doctor / first-boot), never silently per start,
per the migration policy. The Postgres path is proven against a real DB, gated on the URI.
"""

from __future__ import annotations

import os

import pytest
from agentship_langgraph.durability import open_checkpointer
from langgraph.checkpoint.memory import InMemorySaver

_PG = os.environ.get("AGENT_SESSION_STORE_URI")
requires_pg = pytest.mark.skipif(_PG is None, reason="AGENT_SESSION_STORE_URI not set")


async def test_no_conninfo_yields_in_memory_saver() -> None:
    """With no connection string the substrate falls back to an in-memory saver."""
    async with open_checkpointer(None) as cp:
        assert isinstance(cp, InMemorySaver)


async def test_empty_string_conninfo_also_falls_back() -> None:
    """An empty/blank URI is treated as "no database", not a broken Postgres connect."""
    async with open_checkpointer("") as cp:
        assert isinstance(cp, InMemorySaver)


@requires_pg
async def test_conninfo_yields_postgres_saver_with_tables_after_setup() -> None:
    """A URI yields an AsyncPostgresSaver; ``setup=True`` creates its tables (a real query runs)."""
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    async with open_checkpointer(_PG, setup=True) as cp:
        assert isinstance(cp, AsyncPostgresSaver)
        # aget on an unknown thread returns None — and only succeeds if setup() built the tables.
        cfg = {"configurable": {"thread_id": "c41-smoke", "checkpoint_ns": ""}}
        assert await cp.aget(cfg) is None


@requires_pg
async def test_postgres_saver_round_trips_a_checkpoint() -> None:
    """The Postgres saver actually persists and reads back a checkpoint (durability smoke)."""
    from langgraph.checkpoint.base import empty_checkpoint

    async with open_checkpointer(_PG, setup=True) as cp:
        cfg = {"configurable": {"thread_id": "c41-rt", "checkpoint_ns": ""}}
        ckpt = empty_checkpoint()
        saved_cfg = await cp.aput(cfg, ckpt, {}, {})
        got = await cp.aget(saved_cfg)
        assert got is not None and got["id"] == ckpt["id"]
