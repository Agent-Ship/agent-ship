"""The ``ConflictResolver`` primitive: merge specialist outputs deterministically.

When a supervisor fans a turn out to several specialists, their answers must be
reconciled into one. That reconciliation is a **pure, deterministic** decision — a
priority ordering with an explicit tie-break — never an LLM call and never any I/O.
Keeping it pure is what lets the identical-resume conformance cell replay the same
:class:`SpecialistResult` list through :meth:`ConflictResolver.resolve` and assert a
byte-identical result (DESIGN §12).

This module defines the two data shapes the resolver works over — the
config-driven :class:`ConflictPolicy` (parsed from a demo agent's YAML
``conflict_resolver`` block) and the :class:`SpecialistResult` a dispatched
specialist returns. The resolver logic itself lands alongside them.
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict


class ConflictPolicy(BaseModel):
    """How to reconcile competing specialist outputs — a priority list + tie-break.

    ``priority`` orders specialist names highest-preference first; the winning
    output is the survivor earliest in this list. ``on_tie`` decides between
    survivors that share a priority rank: ``"first_by_priority"`` (the default,
    fully deterministic) keeps the first in list order, while
    ``"highest_confidence"`` prefers the larger ``confidence`` and falls back to
    priority order when confidences are equal or absent.

    Parsed from a demo agent's YAML ``conflict_resolver`` block. ``extra="forbid"``:
    an unknown key is a loud error, never a silent typo.
    """

    model_config = ConfigDict(extra="forbid")

    priority: list[str]
    on_tie: Literal["highest_confidence", "first_by_priority"] = "first_by_priority"


class SpecialistResult(TypedDict):
    """One specialist's contribution to a fanned-out turn.

    ``name`` is the specialist's registered agent name; ``output`` is its
    schema-validated structured result. ``confidence`` is an optional self-reported
    score the resolver's ``highest_confidence`` tie-break may consult (``None`` when
    the specialist reports none). ``error`` is set to a message when the specialist
    failed after its retries — such a result is dropped from the merge rather than
    crashing the run (partial-merge, DESIGN §10).
    """

    name: str
    output: dict[str, Any]
    confidence: float | None
    error: str | None


class ConflictResolver:
    """Merge competing specialist outputs into one, purely and deterministically.

    Given a :class:`ConflictPolicy`, :meth:`resolve` picks a single winning
    :class:`SpecialistResult` by priority rank, breaking ties by the policy's
    ``on_tie`` rule. It **never** calls an LLM and does no I/O, so the same inputs
    always yield a byte-identical result — the property the identical-resume
    conformance cell depends on.
    """

    def __init__(self, policy: ConflictPolicy) -> None:
        """Store the priority/tie-break policy this resolver applies."""
        self.policy = policy

    def _rank(self, name: str) -> int:
        """Return a specialist's priority index; unknown names rank last (stable)."""
        try:
            return self.policy.priority.index(name)
        except ValueError:
            return len(self.policy.priority)

    def resolve(self, results: list[SpecialistResult]) -> dict[str, Any]:
        """Pick the winning specialist output; break ties per ``policy.on_tie``.

        Results whose ``error`` is set (or that produced no ``output``) are dropped
        from the merge — a failed specialist never wins, and its failure does not
        crash the run (partial-merge, DESIGN §10). Among the survivors the winner is
        the one with the lowest priority rank; when several share that rank,
        ``highest_confidence`` prefers the largest ``confidence`` (``None`` counts as
        the lowest) while ``first_by_priority`` keeps original list order. Sorting is
        stable so ties resolve identically every time.

        Returns ``{"winner", "output", "considered", "dropped"}`` on success, or
        ``{"winner": None, "status": "no_specialist_succeeded"}`` when no specialist
        produced a usable result.
        """
        survivors = [r for r in results if not r["error"] and r["output"]]
        dropped = [r["name"] for r in results if r["error"] or not r["output"]]

        if not survivors:
            return {"winner": None, "status": "no_specialist_succeeded"}

        best_rank = min(self._rank(r["name"]) for r in survivors)
        candidates = [r for r in survivors if self._rank(r["name"]) == best_rank]

        if self.policy.on_tie == "highest_confidence":
            # max() is stable — the first candidate wins an exact-confidence tie.
            winner = max(
                candidates,
                key=lambda r: r["confidence"] if r["confidence"] is not None else float("-inf"),
            )
        else:  # first_by_priority — keep original order among equal-rank candidates.
            winner = candidates[0]

        return {
            "winner": winner["name"],
            "output": winner["output"],
            "considered": [r["name"] for r in survivors],
            "dropped": dropped,
        }
