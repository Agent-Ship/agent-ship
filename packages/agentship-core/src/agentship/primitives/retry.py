"""Bounded-retry decisions for fanned-out specialists (Phase 02 · C6).

The supervisor retries failed specialists as a **conditional-edge loop with a hard cap** — never
``while True``. This module holds the *decision* as small pure functions the graph wires into that
edge: classify a failure, decide if it is retryable per config, and list which specialists are still
worth (and allowed) a retry. Because the cap is checked here and ``attempts`` lives in checkpointed
graph state, the loop provably terminates — a resumed run continues the retry budget rather than
resetting it, so a crash-loop can't cause infinite retries. Pure and I/O-free; the graph owns the
state, this owns the logic.
"""

from __future__ import annotations

from .conflict_resolver import SpecialistResult

#: A failure category a retry policy can opt into. ``"timeout"`` is a specialist that overran its
#: per-node timeout; ``"specialist_error"`` is any other failure after the specialist's own retries.
RetryCategory = str  # one of {"timeout", "specialist_error"}


def classify_error(error: str | None) -> RetryCategory | None:
    """Map a :class:`SpecialistResult` ``error`` to a retry category, or ``None`` if it succeeded.

    The dispatcher writes a timeout as ``"timed out after …"``; that maps to ``"timeout"``. Every
    other non-empty error maps to the generic ``"specialist_error"``.
    """
    if not error:
        return None
    if error.startswith("timed out"):
        return "timeout"
    return "specialist_error"


def is_retryable(result: SpecialistResult, retry_on: list[RetryCategory]) -> bool:
    """Whether ``result``'s failure is one the policy (``retry_on``) says to retry."""
    category = classify_error(result["error"])
    return category is not None and category in retry_on


def specialists_to_retry(
    results: list[SpecialistResult],
    attempts: dict[str, int],
    max_attempts: int,
    retry_on: list[RetryCategory],
) -> list[str]:
    """Names of specialists from the last pass that are retryable AND still under the cap.

    ``attempts`` counts passes already made per specialist; a name at or above ``max_attempts`` is
    exhausted and excluded, which is what guarantees the loop terminates.
    """
    return [
        r["name"]
        for r in results
        if is_retryable(r, retry_on) and attempts.get(r["name"], 0) < max_attempts
    ]


def should_retry(
    results: list[SpecialistResult],
    attempts: dict[str, int],
    max_attempts: int,
    retry_on: list[RetryCategory],
) -> bool:
    """``True`` while at least one specialist can still be retried, else ``False`` (→ merge).

    The graph's ``dispatch_router`` is a one-liner over this: ``"retry" if should_retry(...) else
    "merge"``.
    """
    return bool(specialists_to_retry(results, attempts, max_attempts, retry_on))
