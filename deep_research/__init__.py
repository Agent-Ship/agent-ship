"""The deep-research demo: a long-running, checkpointed, human-in-the-loop research agent.

This package is the flagship "long agent" example. Unlike the quick-search agent (a single
ReAct turn that answers in seconds), the deep-research agent runs an *iterative* loop — plan
sub-queries, search, reflect, deepen, search again — and after a few automatic rounds it
**pauses to ask the human** "go deeper?" before spending more effort. Because it declares
``durability: checkpoint``, every round is checkpointed, so the run survives a crash or a long
wait for the human's reply and resumes exactly where it left off.

The loop is authored natively in LangGraph (see :mod:`deep_research.graph`) — AgentShip owns the
checkpoint/interrupt/resume machinery, this graph owns the research strategy. The web search
itself is :func:`deep_research.web_search.search_web`.
"""

from __future__ import annotations

from .graph import DeepResearchAgent, build_agent

__all__ = ["DeepResearchAgent", "build_agent"]
