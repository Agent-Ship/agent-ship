"""The LangGraph engine package — AgentShip's default real-model engine.

:class:`~agentship.engines.langgraph.engine.LangGraphEngine` builds a minimal
single-agent LangGraph graph (system prompt + user input → chat model → answer)
over a LiteLLM-backed model resolved by :mod:`agentship.models`. It ships in the
``[langgraph]`` extra and is discovered via the ``agentship.engines`` entry-point
group, so ``engine: langgraph`` in a spec resolves here.
"""

from __future__ import annotations
