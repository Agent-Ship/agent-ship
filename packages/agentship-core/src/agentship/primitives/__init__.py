"""Small, vendor-free decision primitives the harness composes.

A *primitive* is a tiny, deterministic, dependency-light helper the runtime uses at
request time — never an LLM, never a vendor call. The first is the
:class:`~agentship.primitives.model_router.ModelRouter`, which chooses which model
id a turn should use. Primitives live in core so every engine adapter reads the same
decisions; they are swappable via entry-point registries the same way engines are.
"""

from __future__ import annotations

from .dispatch import AgentRef
from .idempotency import (
    DictLedger,
    IdempotencyLedger,
    LedgerEntry,
    call_once,
    canonical_json,
    idem_key,
)
from .model_router import (
    DefaultModelRouter,
    LookupModelRouter,
    ModelRouter,
    resolve_model_router,
    stamp_routed_model,
)
from .retry import should_retry, specialists_to_retry

__all__ = [
    "AgentRef",
    "DefaultModelRouter",
    "DictLedger",
    "IdempotencyLedger",
    "LedgerEntry",
    "LookupModelRouter",
    "ModelRouter",
    "call_once",
    "canonical_json",
    "idem_key",
    "resolve_model_router",
    "should_retry",
    "specialists_to_retry",
    "stamp_routed_model",
]
