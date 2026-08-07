"""The custom-authoring base class: :class:`LangGraphAgent`.

This is the answer to CRUX 1 (DESIGN §4.3): *"how do I build a custom LangGraph
agent while still getting AgentShip's wired services?"* A developer subclasses
:class:`LangGraphAgent` and writes native LangGraph in :meth:`build_graph` — nodes,
edges, conditional routing, subgraphs, anything LangGraph does. The engine's
``build(spec)`` resolves the ``model`` and ``tools`` and hands them to
``build_graph`` as plain, already-wired params; the author never wires a vendor
themselves and never loses native power. Per-request services (memory recall,
tracing, caller identity) are read from ``RunContext`` inside the nodes, not passed
in a bundle — there is deliberately no ``EngineKit``.

The author points a spec's ``code:`` at a factory that returns a configured
subclass instance (which carries its own ``.spec``); the core threads that object
to :meth:`~agentship_langgraph.engine.LangGraphEngine.build`, which calls
``build_graph``. Every LangChain/LangGraph type stays inside this adapter — the
vendor-neutral core never sees one.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from agentship.errors import CapabilityError

if TYPE_CHECKING:
    from agentship.spec import AgentSpec
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.tools import BaseTool
    from langgraph.graph import StateGraph


class LangGraphAgent(ABC):
    """Base class a developer subclasses to author a native LangGraph agent.

    A subclass implements :meth:`build_graph` in native LangGraph; the engine
    binding is fixed to ``"langgraph"`` and ``run``/``stream``/``resume`` are
    provided by the engine adapter (the author never writes them). Construct one
    with its :class:`~agentship.spec.AgentSpec`; the spec's ``engine`` must be
    ``"langgraph"`` or construction fails fast with a
    :class:`~agentship.errors.CapabilityError` (never a silent mis-bind).
    """

    #: The engine this agent is bound to. Fixed for every LangGraph agent so the
    #: subclass never has to restate it; the spec's ``engine`` must still match.
    engine_name = "langgraph"

    def __init__(self, spec: AgentSpec) -> None:
        """Bind ``spec`` to this agent, asserting its engine is ``langgraph``.

        The custom-authoring path only makes sense when the spec targets this
        engine; a spec whose ``engine`` is anything else is a misconfiguration, so
        it fails fast here (declare, don't fake) rather than compiling a graph the
        wrong engine would try to drive.
        """
        if spec.engine != self.engine_name:
            raise CapabilityError(
                f"{type(self).__name__} is a LangGraph agent, but its spec sets "
                f"engine: {spec.engine!r} — set engine: {self.engine_name!r} or use the "
                f"matching engine's agent base class."
            )
        self.spec = spec

    @abstractmethod
    def build_graph(self, model: BaseChatModel, tools: list[BaseTool]) -> StateGraph:
        """Return a native LangGraph ``StateGraph`` built over the wired pieces.

        The engine passes ``model`` (a LiteLLM-backed, cost-traced chat model it
        resolved from the spec / ``RunContext.routed_model``) and ``tools`` (adapted
        MCP/python tools — an empty list until Phase 03 wires tool execution). Build
        and return your graph using them; the engine compiles it (attaching the
        checkpointer/store it resolved, plus callbacks) and drives ``run``/``stream``.
        You may return an already-compiled graph — the engine detects that and reuses
        it. Read per-request services (memory, tracing, caller identity) from
        ``RunContext`` via ``get_run_context()`` inside your nodes.
        """
