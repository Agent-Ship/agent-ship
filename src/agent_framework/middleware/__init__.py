"""Engine-agnostic middleware.

Middleware wraps an `AgentEngine` (via `MiddlewareEngine`) to add cross-
cutting concerns — memory, RAG, tracing, safety — without coupling those
concerns to any specific engine/SDK.

Modules:
- base              : `EngineMiddleware` ABC — the contract every
                      middleware implements.
- memory_middleware : `MemoryMiddleware` — long-term memory recall/write
                      hooks. Constants `MEMORY_RECALL_KEY` and
                      `MEMORY_RECALL_PREAMBLE` live here because they
                      describe what this middleware writes into the per-
                      request `request_context` dict for prompt builders
                      to read.
"""

from src.agent_framework.middleware.base import EngineMiddleware
from src.agent_framework.middleware.memory_middleware import (
    MEMORY_RECALL_KEY,
    MEMORY_RECALL_PREAMBLE,
    MemoryMiddleware,
)

__all__ = [
    "EngineMiddleware",
    "MEMORY_RECALL_KEY",
    "MEMORY_RECALL_PREAMBLE",
    "MemoryMiddleware",
]
