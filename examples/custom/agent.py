"""A custom LangGraph agent authored in native LangGraph (the CRUX-1 path).

This is the smallest possible demonstration of custom authoring: subclass
:class:`~agentship_langgraph.agent.LangGraphAgent`, write a real ``StateGraph`` in
``build_graph(model, tools)``, and let the harness wire the model, drive
``run``/``stream``, and manage the context. A spec points its ``code:`` at
:func:`build_agent` below; ``agentship run examples/custom/custom.yaml`` loads it.
"""

from __future__ import annotations

from agentship.spec import AgentSpec
from agentship_langgraph import LangGraphAgent
from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict


class _State(TypedDict):
    """The graph state: the running chat message list for one turn."""

    messages: list


class EchoingAssistant(LangGraphAgent):
    """A one-node custom agent that answers with the wired model.

    Trivial on purpose — the point is that ``build_graph`` is *native* LangGraph
    (nodes, edges, state) while AgentShip supplies the ``model`` and drives the run.
    A real agent would add routing, tools, subgraphs, or ``interrupt()`` here.
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


def build_agent() -> EchoingAssistant:
    """Return the configured custom agent (referenced from ``custom.yaml``'s ``code:``)."""
    return EchoingAssistant(
        AgentSpec(
            name="custom-assistant",
            engine="langgraph",
            model="openai/gpt-4o-mini",
            prompt="You are a concise assistant. Answer in one short sentence.",
        )
    )
