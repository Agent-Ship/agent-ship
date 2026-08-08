"""A custom LangGraph agent authored in native LangGraph (the custom `build_graph` path).

This is the smallest honest demonstration of custom authoring: subclass
:class:`~agentship_langgraph.agent.LangGraphAgent`, write a real ``StateGraph`` in
``build_graph(model, tools)``, and let the harness wire the model, drive
``run``/``stream``, and manage the run context. The demo's ``agents/custom/custom.yaml``
points its ``code:`` at :func:`build_agent` below, so
``agentship run agents/custom/custom.yaml`` loads and runs *the author's* graph.

The node is deliberately trivial (one ``answer`` node). The point is not the
node's cleverness but that ``build_graph`` is *native* LangGraph — nodes, edges,
state — while AgentShip supplies the ``model`` and drives the turn. A real agent
would add routing, tools, subgraphs, or ``interrupt()`` here without losing any
native power.
"""

from __future__ import annotations

from agentship.spec import AgentSpec
from agentship_langgraph import LangGraphAgent
from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict


class _State(TypedDict):
    """The graph state: the running chat message list for one turn."""

    messages: list


class NativeAssistant(LangGraphAgent):
    """A one-node custom agent that answers with the wired model.

    Trivial on purpose — the demo's assertion is that this *author-written* node
    is the one that executes (not the engine's default build body), proving custom
    authoring end to end.
    """

    def build_graph(self, model, tools) -> StateGraph:
        """Build a native ``START -> answer -> END`` graph over the wired model."""

        def answer(state: _State) -> dict:
            """Invoke the wired model on the current messages, appending its reply."""
            reply = model.invoke(state["messages"])
            return {"messages": [*state["messages"], reply]}

        g = StateGraph(_State)
        g.add_node("answer", answer)
        g.add_edge(START, "answer")
        g.add_edge("answer", END)
        return g


def build_agent() -> NativeAssistant:
    """Return the configured custom agent (referenced from ``custom.yaml``'s ``code:``)."""
    return NativeAssistant(
        AgentSpec(
            name="custom-assistant",
            engine="langgraph",
            model="openai/gpt-4o-mini",
            prompt="You are a concise assistant. Answer in one short sentence.",
        )
    )
