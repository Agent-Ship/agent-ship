"""Middleware protocol for engines.

A middleware sits between `BaseAgent.run()` and the actual engine, on the
seam exposed by `MiddlewareEngine`. It can inspect / transform the input
before the engine runs, and observe the output after.

Both hooks receive a `request_context` dict — a per-request scratchpad
shared across middlewares and the inner engine. Middlewares write values
into it (e.g. `MemoryMiddleware` writes a recall block under
`MEMORY_RECALL_KEY`); the engine reads from it when building prompts.
The dict is created fresh per request by `MiddlewareEngine` and
discarded when the call returns, so writes from one request cannot leak
into another.
"""

from __future__ import annotations

import abc
from typing import Any, Dict, Optional

from pydantic import BaseModel


class EngineMiddleware(abc.ABC):
    """Engine-agnostic middleware contract.

    A middleware can:
    - inspect/transform `input_data` before the engine runs
    - write side-channel data into `request_context` for the engine to read
    - observe the output after the engine runs (for writeback/telemetry)
    """

    @abc.abstractmethod
    async def before_run(
        self,
        *,
        user_id: str,
        session_id: str,
        input_data: BaseModel,
        request_context: Optional[Dict[str, Any]] = None,
    ) -> BaseModel:
        """Run before the engine. Return the (possibly transformed) input_data.

        Writes into `request_context` (when provided) flow to the engine
        on the same call; they do not persist across requests.
        """

    @abc.abstractmethod
    async def after_run(
        self,
        *,
        user_id: str,
        session_id: str,
        input_data: BaseModel,
        output_data: Any,
        request_context: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Run after the engine. Observe the result (no return).

        The same `request_context` instance from `before_run` is passed
        here, so a middleware can read what it wrote earlier.
        """
