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
