"""The LangGraph engine (skeleton) — registered so the kernel can discover it.

This file is fleshed out in Task 3 (the real single-agent graph over a LiteLLM
model). At Task 1 it exists only so the ``agentship.engines`` entry point
resolves and the registry lists ``langgraph`` alongside ``echo``.
"""

from __future__ import annotations

from typing import ClassVar

from ..base import Engine, EngineCapabilities


class LangGraphEngine(Engine):
    """AgentShip's default engine — a single-agent LangGraph graph over LiteLLM.

    Declares ``streaming`` only for now; ``tool_calling``/``structured_output``/
    ``multi_agent`` stay ``False`` so the capability gate honestly rejects those
    (they arrive in later phases). The build/run/stream body lands in Task 3.
    """

    name: ClassVar[str] = "langgraph"
    capabilities: ClassVar[EngineCapabilities] = EngineCapabilities(streaming=True)

    def build(self, spec):  # noqa: ANN001, ANN201 - fleshed out in Task 3
        """Compile the spec into a runnable graph — implemented in Task 3."""
        raise NotImplementedError("LangGraphEngine.build lands in Phase 1 Task 3")

    async def run(self, compiled, text, ctx):  # noqa: ANN001, ANN201 - fleshed out in Task 3
        """Run one turn — implemented in Task 3."""
        raise NotImplementedError("LangGraphEngine.run lands in Phase 1 Task 3")
