"""Thin re-export of the shipped, vendor-neutral conformance catalogue.

The capability catalogue now lives in the core package as the shipped
:mod:`agentship.conformance` module (so ``agentship verify`` can import it). This
test-tree module re-exports it unchanged so the existing conformance tests keep
their ``from conformance.capabilities import …`` imports. New code should import
from :mod:`agentship.conformance` directly.
"""

from __future__ import annotations

from agentship.conformance import (
    CAPABILITIES,
    DEFERRED_CAPABILITIES,
    Capability,
    CellResult,
    covered_capability_names,
    request_capability,
    run_capability_grid,
)

__all__ = [
    "CAPABILITIES",
    "Capability",
    "CellResult",
    "DEFERRED_CAPABILITIES",
    "covered_capability_names",
    "request_capability",
    "run_capability_grid",
]
