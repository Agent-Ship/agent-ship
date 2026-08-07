"""The LangGraph engine package — AgentShip's default real-model engine.

:class:`~agentship_langgraph.engine.LangGraphEngine` builds the agent — either its
own default single-agent graph (system prompt + user input → chat model → answer)
or, when a developer subclasses :class:`~agentship_langgraph.agent.LangGraphAgent`
and implements ``build_graph(model, tools)``, that author's native graph — over a
LiteLLM-backed model resolved by :mod:`agentship_langgraph.models`. It ships as the
``agentship-langgraph`` dist and is discovered via the ``agentship.engines``
entry-point group, so ``engine: langgraph`` in a spec resolves here.
"""

from __future__ import annotations

from .agent import LangGraphAgent
from .engine import LangGraphEngine

__all__ = ["LangGraphAgent", "LangGraphEngine"]
