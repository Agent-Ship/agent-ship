"""Long-term memory framework.

Modules:
- base    : `LongTermMemory` ABC and `Memory*` data types — the contract
            every backend adapter must implement.
- factory : `MemoryFactory` — turns the agent's YAML config into a live
            backend (or None when memory is disabled).

Backend adapters live under `backends/` and are picked up by the factory.
Agent code never imports them directly.

The middleware that wires this backend into an agent run lives in
`agent_framework.middleware.memory_middleware` — middleware
implementations are grouped with the rest of the `EngineMiddleware`
family, not here.
"""

from src.agent_framework.memory.base import (
    LongTermMemory,
    MemoryKind,
    MemoryRecord,
    MemoryScope,
    MemorySearchQuery,
    MemorySource,
    MemoryWrite,
)
from src.agent_framework.memory.factory import MemoryFactory

__all__ = [
    "LongTermMemory",
    "MemoryFactory",
    "MemoryKind",
    "MemoryRecord",
    "MemoryScope",
    "MemorySearchQuery",
    "MemorySource",
    "MemoryWrite",
]
