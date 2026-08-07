"""The LangGraph engine package — AgentShip's default real-model engine.

:class:`~agentship_langgraph.engine.LangGraphEngine` builds a minimal single-agent
LangGraph graph (system prompt + user input → chat model → answer) over a
LiteLLM-backed model resolved by :mod:`agentship_langgraph.models`. It ships as the
``agentship-langgraph`` dist and is discovered via the ``agentship.engines``
entry-point group, so ``engine: langgraph`` in a spec resolves here.
"""

from __future__ import annotations

from .engine import LangGraphEngine

__all__ = ["LangGraphEngine"]
