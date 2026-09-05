"""The ``graph`` template: a fillable multi-agent *supervisor scaffold*.

``template: graph`` gives an author a real, compilable starting point for a
controlled multi-agent agent: a **coordinator** node that decides which worker
handles a turn, routed by a conditional edge to a **worker** node that answers. It
is deliberately minimal — one coordinator, one worker — and marked with
``# TODO(author)`` where specialists, tools, and richer routing get filled in. This
is the authoring scaffold only; a full *durable* multi-agent runtime (checkpointed
sub-graphs, HITL, parallel workers) is Phase 02.

The scaffold's state carries the ``messages`` list the engine seeds and reads (so
``run``/``stream`` map its output the same way they do for every other template)
plus a ``route`` key the coordinator sets and the conditional edge switches on. The
coordinator and worker both call the wired ``model`` the engine passed in; per-turn
services (memory, tracing, caller identity) are read from ``RunContext`` inside the
nodes, exactly as in a custom :class:`~agentship_langgraph.agent.LangGraphAgent`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

if TYPE_CHECKING:
    from agentship.spec import AgentSpec
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.tools import BaseTool


class SupervisorState(TypedDict):
    """The supervisor scaffold's state: the message history plus the chosen route.

    ``messages`` uses the ``add_messages`` reducer so each node appends rather than
    replaces (the engine seeds it with ``[system, user]`` and reads the final
    message as the answer). ``route`` is the coordinator's decision, switched on by
    the conditional edge. Add your own state keys here as you grow the team.
    """

    messages: Annotated[list, add_messages]
    #: The worker the coordinator routed to (``"worker"`` or ``"done"`` in the
    #: scaffold). TODO(author): widen to your specialist names.
    route: str


def build_graph_template(spec: AgentSpec):
    """Return a ``build_graph(model, tools)`` body for the ``graph`` template.

    The returned callable builds the supervisor scaffold over the wired ``model``
    and ``tools``: a coordinator that picks a route and a worker that answers, wired
    ``START → coordinator → (conditional) → worker → END``. It closes over ``spec``
    only for the prompt. The engine compiles the returned :class:`StateGraph`
    (attaching any checkpointer/store it resolved), so this body returns the graph
    uncompiled — the fillable seam an author edits.
    """

    def build_graph(model: BaseChatModel, tools: list[BaseTool]) -> StateGraph:
        """Build the coordinator→worker supervisor scaffold over the wired pieces."""

        def coordinator(state: SupervisorState) -> dict:
            """Decide which worker handles this turn (the routing decision).

            TODO(author): replace this with your real routing — inspect the request,
            pick among several specialists, and set ``route`` to the chosen worker's
            node name. The scaffold asks the model for a one-word route and always
            falls through to the single ``worker``.
            """
            decision = model.invoke(state["messages"])
            route = (decision.content or "worker").strip().lower()
            # The scaffold only knows one worker; unknown routes fall through to it.
            if route != "done":
                route = "worker"
            return {"route": route}

        def worker(state: SupervisorState) -> dict:
            """Answer the request, appending the reply to the message history.

            TODO(author): add tools (``model.bind_tools(tools)``), specialist
            prompts, or a sub-graph here. The scaffold just invokes the wired model
            on the running conversation and returns its reply.
            """
            reply = model.invoke(state["messages"])
            return {"messages": [reply]}

        builder: StateGraph = StateGraph(SupervisorState)
        builder.add_node("coordinator", coordinator)
        builder.add_node("worker", worker)
        builder.add_edge(START, "coordinator")
        # TODO(author): add more branches as you add specialists.
        builder.add_conditional_edges(
            "coordinator",
            lambda state: state["route"],
            {"worker": "worker", "done": END},
        )
        builder.add_edge("worker", END)
        return builder

    return build_graph
