"""T2 proof: the custom ``build_graph`` authoring path, offline with a fake model.

A developer subclasses :class:`~agentship_langgraph.agent.LangGraphAgent` and writes
native LangGraph in ``build_graph(model, tools)``. The engine's ``build(spec)``
resolves the model + tools and calls that method, then compiles the result and
drives it through ``run``. These tests prove the whole seam end to end with a fake
chat model injected (no network) — and that it is the *author's* graph running, not
the engine's default single-node graph.
"""

from __future__ import annotations

import agentship_langgraph.models as models_module
import pytest
from agentship.errors import CapabilityError
from agentship.runtime import build_agent
from agentship.spec import AgentSpec
from agentship_langgraph import LangGraphAgent
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict


class _State(TypedDict):
    """Minimal graph state carrying the running message list plus a marker."""

    messages: list
    marked: bool


class ShoutAgent(LangGraphAgent):
    """A custom agent whose native graph adds a node the engine's default never runs.

    ``build_graph`` wires a two-node graph: a ``mark`` node stamps ``marked=True``
    into the state, then a ``call`` node invokes the wired model. The ``marked``
    flag is what proves the *author's* graph ran (the engine's default single-node
    graph has no such node), so this test cannot pass by accident against the
    stock build path.
    """

    def build_graph(self, model, tools) -> StateGraph:
        """Build a native two-node LangGraph over the wired model."""

        def mark(state: _State) -> dict:
            """Stamp a marker proving this custom node executed."""
            return {"marked": True}

        def call(state: _State) -> dict:
            """Invoke the wired model on the current messages, appending the reply."""
            reply = model.invoke(state["messages"])
            return {"messages": [*state["messages"], reply]}

        g = StateGraph(_State)
        g.add_node("mark", mark)
        g.add_node("call", call)
        g.add_edge(START, "mark")
        g.add_edge("mark", "call")
        g.add_edge("call", END)
        return g


def build_shout_agent() -> ShoutAgent:
    """A ``code:`` factory that returns a configured custom LangGraph agent.

    Referenced from a spec via ``code: "<this module>:build_shout_agent"``; the
    returned agent carries its own :class:`AgentSpec` so the harness knows how to
    build it.
    """
    return ShoutAgent(
        AgentSpec(name="shout", engine="langgraph", model="x", prompt="You are loud.")
    )


CODE_REF = f"{__name__}:build_shout_agent"


@pytest.fixture
def fake_model(monkeypatch):
    """Inject a deterministic fake chat model so the custom graph runs offline."""
    fake = FakeListChatModel(responses=["HELLO!"])
    monkeypatch.setattr(models_module, "resolve_model", lambda *a, **k: fake)
    return fake


async def test_custom_build_graph_runs_end_to_end(fake_model):
    """A LangGraphAgent subclass's build_graph runs via build_agent + run()."""
    agent = build_agent(AgentSpec(name="shout", engine="langgraph", code=CODE_REF))
    result = await agent.run("hi")
    assert result.output == "HELLO!"


async def test_it_is_the_authors_graph_that_runs(fake_model):
    """The author's custom node executes — proving it is not the engine's default graph.

    The ``mark`` node sets ``marked=True`` in the final state; the engine's stock
    single-node graph has no such node, so observing the marker proves the wiring
    dispatched to the author's ``build_graph``, not the default build body. If the
    custom-authoring wiring were removed, the engine would fall back to (or fail on)
    its default path and this marker would never be set — making the test non-vacuous.
    """
    built = build_agent(AgentSpec(name="shout", engine="langgraph", code=CODE_REF))
    # Reach the compiled graph and invoke it directly to inspect the full state.
    state = await built.compiled.graph.ainvoke(
        {"messages": built.compiled.initial_messages("hi"), "marked": False}
    )
    assert state["marked"] is True
    assert state["messages"][-1].content == "HELLO!"


def build_wrong_engine_agent() -> ShoutAgent:
    """A factory returning a custom agent whose spec names a non-langgraph engine."""
    return ShoutAgent(AgentSpec(name="w", engine="echo", model="x"))


def test_custom_agent_on_wrong_engine_is_rejected():
    """A LangGraphAgent whose spec.engine != langgraph fails fast, never silently binds."""
    ref = f"{__name__}:build_wrong_engine_agent"
    with pytest.raises(CapabilityError):
        build_agent(AgentSpec(name="w", engine="echo", code=ref))
