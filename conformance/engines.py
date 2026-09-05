"""Thin re-export of the per-engine offline harness (now shipped in the engine package).

The offline harness patches a model-backed engine's model seam to a deterministic
fake so a positive conformance cell never touches the network. Because it needs a
vendor (LangChain) type, it now lives in the engine package as a supported helper,
:mod:`agentship_langgraph.testing`, keeping the vendor-neutral catalogue
(:mod:`agentship.conformance`) free of vendor imports. This test-tree module
re-exports :func:`~agentship_langgraph.testing.offline` so existing conformance
tests keep their ``from conformance.engines import offline`` imports.
"""

from __future__ import annotations

from agentship_langgraph.testing import OFFLINE_HARNESSES, offline

__all__ = ["OFFLINE_HARNESSES", "offline"]
