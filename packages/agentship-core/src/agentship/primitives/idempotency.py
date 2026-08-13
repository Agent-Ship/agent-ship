"""The deterministic tool-idempotency key — the ONE formula shared across phases.

A tool call is identified by ``sha256(thread_id | node_id | tool_name | canonical_json(args))``
(design §C8 / §13.6). This is the single definition: P02 emits the key into the checkpoint
``tool_ledger`` and P09 persists it in the durable ``tool_idempotency_keys`` table — both look up
by the byte-identical value produced here, so a resumed or reclaimed run never re-fires a tool's
side effect. The key must therefore be **stable across argument key-order** (callers may build the
same ``args`` dict in any order) and **deterministic** (same inputs → same bytes, always), which is
exactly what :func:`canonical_json` guarantees. This module is pure — no I/O, no model, no clock.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

#: Field separator between the key's parts. A control byte (ASCII Unit Separator) that will not
#: appear in ordinary ids/tool names, so ``("ab", "c")`` and ``("a", "bc")`` can never collide
#: into the same joined payload.
_FIELD_SEP = "\x1f"


def canonical_json(value: Any) -> str:
    """Serialize ``value`` to a deterministic, whitespace-free JSON string.

    Object keys are sorted (recursively) so dicts built in different orders serialize
    identically; list order is preserved because it is semantic. ``ensure_ascii`` keeps the
    output byte-stable across platforms/locales. Non-JSON-serializable values raise ``TypeError``,
    surfacing a bad tool-arg early rather than hashing something unstable.
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def idem_key(thread_id: str, node_id: str, tool_name: str, args: Any) -> str:
    """Return the canonical idempotency key for one tool call as a hex sha256 digest.

    The four parts are joined with :data:`_FIELD_SEP` and hashed; ``args`` is run through
    :func:`canonical_json` first so key order does not change the result. The returned value is
    the exact key P02's checkpoint mirror and P09's durable ledger look up by — do not change the
    layout without updating both.
    """
    payload = _FIELD_SEP.join([thread_id, node_id, tool_name, canonical_json(args)])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
