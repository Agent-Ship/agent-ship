"""The graph factory the generated Studio wrapper calls (§4.8).

``agentship studio`` writes a ``langgraph.json`` manifest plus a wrapper module that binds each
agent's YAML spec to a graph by calling :func:`build_studio_graph`. LangGraph Studio (``langgraph
dev``) imports that graph and manages its own checkpointer, so this returns the *uncompiled*
``StateGraph`` builder — the same seam durable runs recompile — letting Studio bind its checkpointer
for time-travel over checkpoints.

Kept in the langgraph package (not core) because it imports the engine; core only references it by
string in the generated manifest, so the vendor-free kernel stays LangGraph-free.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agentship.spec import load_spec

from .engine import LangGraphEngine


def build_studio_graph(spec_path: str) -> Any:
    """Return the uncompiled ``StateGraph`` builder for the agent spec at ``spec_path``.

    Loads and validates the spec, compiles it with the LangGraph engine, and hands back the
    ``builder`` behind the compiled graph so LangGraph Studio can recompile it with its own
    checkpointer. Raises the usual :class:`~agentship.errors.SpecError`/``CapabilityError`` on an
    invalid spec, surfaced by ``langgraph dev`` at import time.
    """
    spec = load_spec(Path(spec_path))
    compiled = LangGraphEngine().build(spec)
    return compiled.builder
