"""Postgres-backed session store using LangGraph AsyncPostgresSaver."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

_CHECKPOINTER = None
_CHECKPOINTER_CTX = None
_LOCK = asyncio.Lock()
_INITIALIZED = False


async def get_checkpointer(db_url: Optional[str] = None):
    """Return a singleton LangGraph checkpointer (Postgres or InMemory)."""
    global _CHECKPOINTER, _CHECKPOINTER_CTX, _INITIALIZED

    if _INITIALIZED:
        return _CHECKPOINTER

    async with _LOCK:
        if _INITIALIZED:
            return _CHECKPOINTER

        url = db_url or os.environ.get("AGENTSHIP_DATABASE_URL") or os.environ.get("DATABASE_URL")
        if url:
            try:
                from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
                logger.info("Initializing Postgres checkpointer at %s", url)
                _CHECKPOINTER_CTX = AsyncPostgresSaver.from_conn_string(url)
                _CHECKPOINTER = await _CHECKPOINTER_CTX.__aenter__()
                await _CHECKPOINTER.setup()
                logger.info("Postgres checkpointer ready")
            except Exception as exc:
                logger.warning("Postgres checkpointer failed (%s) — falling back to InMemory", exc)
                _CHECKPOINTER = _in_memory()
        else:
            logger.info("No database URL — using InMemory checkpointer")
            _CHECKPOINTER = _in_memory()

        _INITIALIZED = True
        return _CHECKPOINTER


def _in_memory():
    from langgraph.checkpoint.memory import InMemorySaver
    return InMemorySaver()


def reset():
    """Reset singleton — for testing."""
    global _CHECKPOINTER, _CHECKPOINTER_CTX, _INITIALIZED
    _CHECKPOINTER = None
    _CHECKPOINTER_CTX = None
    _INITIALIZED = False
