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
import inspect
import json
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel

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


class LedgerEntry(BaseModel):
    """One row of the write-ahead intent ledger: a tool call's status and recorded result.

    ``status`` is ``"pending"`` once the intent is written but before the side effect is confirmed
    ``done``. A ``pending`` entry seen on resume means "the effect may have happened — verify, do
    not blindly re-fire" (§13.6). ``result`` holds the recorded return value once ``done``.
    """

    status: Literal["pending", "done"]
    result: Any = None


@runtime_checkable
class IdempotencyLedger(Protocol):
    """The seam `call_once` reads/writes, exposed as ``RunContext.idempotency`` (§13.2, §13.6).

    P02 provides an in-memory/checkpoint-mirrored implementation (:class:`DictLedger`); P09 swaps
    in a durable Postgres-backed one under the SAME contract, so the byte-identical
    :func:`idem_key` looks up the same entry across a crash. Just two operations: read an entry,
    write an entry.
    """

    def lookup(self, key: str) -> LedgerEntry | None:
        """Return the recorded entry for ``key``, or ``None`` if this call has not been seen."""
        ...

    def record(self, key: str, entry: LedgerEntry) -> None:
        """Persist ``entry`` for ``key`` (overwriting any prior status for that key)."""
        ...


class DictLedger:
    """A plain in-memory :class:`IdempotencyLedger` backed by a dict (P02's default).

    This is what lives inside the graph's checkpoint state during a run; P09 replaces it with a
    durable store implementing the same protocol. Deliberately trivial — the exactly-once logic
    lives in :func:`call_once`, not in the storage.
    """

    def __init__(self, entries: dict[str, LedgerEntry] | None = None) -> None:
        """Start empty, or seed with existing ``entries`` (e.g. re-hydrated from a checkpoint)."""
        self._entries: dict[str, LedgerEntry] = dict(entries) if entries else {}

    def lookup(self, key: str) -> LedgerEntry | None:
        """Return the entry for ``key`` if present, else ``None``."""
        return self._entries.get(key)

    def record(self, key: str, entry: LedgerEntry) -> None:
        """Store ``entry`` under ``key``."""
        self._entries[key] = entry


async def _maybe_await(value: Any) -> Any:
    """Return ``value``, awaiting it first if it is a coroutine (effects may be sync or async)."""
    if inspect.isawaitable(value):
        return await value
    return value


async def call_once(ledger: IdempotencyLedger, key: str, fn: Any, *, idempotent: bool) -> Any:
    """Run ``fn`` at most once per ``key``, exactly-once-safe across resume (§13.6).

    Resolution:

    - **Recorded ``done``** → return the stored result; ``fn`` is never called (memoized replay).
    - **Recorded ``pending``** → a crash happened after the write-ahead but before ``done``; call
      ``fn.verify()`` to check whether the side effect already landed, record ``done`` with its
      result, and return it — never a blind re-fire.
    - **Unseen + ``idempotent=True``** → call ``fn``, record ``done``; safe to re-run, but recorded
      to short-circuit next time.
    - **Unseen + ``idempotent=False``** (a write) → record ``pending`` *before* calling ``fn`` (the
      write-ahead), then call ``fn`` and record ``done``. If ``fn`` raises, the ``pending`` marker
      survives so a later resume takes the verify path instead of re-firing.

    ``fn`` is a callable object; on the pending path its ``verify()`` is used instead. Either may be
    sync or async. ``ledger`` is the :class:`IdempotencyLedger` (``RunContext.idempotency``).
    """
    existing = ledger.lookup(key)
    if existing is not None:
        if existing.status == "done":
            return existing.result
        verified = await _maybe_await(fn.verify())
        ledger.record(key, LedgerEntry(status="done", result=verified))
        return verified

    if not idempotent:
        # write-ahead: the intent is durable before the side effect fires
        ledger.record(key, LedgerEntry(status="pending"))
    result = await _maybe_await(fn())
    ledger.record(key, LedgerEntry(status="done", result=result))
    return result
