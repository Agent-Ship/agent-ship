"""Spans must be readable in a backend, and a conversation must group into a thread.

Two things a trace UI shows you: the span's NAME, and whatever it can use to group related
traces. We were giving it neither.

Every agent span was literally named "agent", so an Opik trace of a supervisor and its three
specialists read as four rows called "agent" — the one detail that would tell them apart, the
agent's name, sat in an attribute the UI does not surface. And nothing carried `thread_id`, the
attribute Opik groups traces into threads by, so a ten-turn conversation appeared as ten
unrelated traces.
"""

from __future__ import annotations

from agentship.observability import semconv
from agentship.observability.recorder import RecordingObserver
from agentship.runtime import build_agent
from agentship.spec import AgentSpec


async def _root(name: str):
    """Run one turn of an echo agent and return its root span."""
    agent = build_agent(AgentSpec(name=name, engine="echo"))
    observer = RecordingObserver()
    agent.observer = observer
    await agent.run("hello", session_id="s-thread-1")
    return observer.roots[0]


async def test_the_agent_span_is_named_after_the_agent():
    """A reader can tell two agents apart in a trace list without opening them."""
    root = await _root("research-team")
    assert "research-team" in root.name, (
        f"the span name must identify the agent; got {root.name!r} — a supervisor and its "
        f"specialists would all read as the same row"
    )


async def test_the_agent_name_is_still_an_attribute():
    """The machine-readable attribute stays, so anything querying by it keeps working."""
    root = await _root("research-team")
    assert root.attrs[semconv.AS_AGENT_NAME] == "research-team"


async def test_the_session_is_exposed_as_thread_id_for_backends():
    """``thread_id`` is what Opik groups traces into threads by, so the session id goes there.

    Deliberately the bare key `thread_id`, not a namespaced one: it is what the backend reads.
    Our own `agentship.session.id` stays alongside it for anything querying our namespace.
    """
    root = await _root("research-team")
    assert root.attrs.get(semconv.THREAD_ID) == "s-thread-1"
    assert root.attrs[semconv.AS_SESSION_ID] == "s-thread-1"


# ---- readable model spans, and no bookkeeping noise --------------------------------------------


def test_a_model_span_says_which_model_ran():
    """``chat gpt-4o-mini`` rather than ``model``.

    A trace of a supervisor showed several rows all called "model" and no way to tell which
    ran on the cheap classifier and which on the expensive specialist without opening each
    one. OTel's GenAI convention names a model span ``{operation} {model}`` for this reason.
    """
    assert semconv.model_span("openai/gpt-4o-mini") == "chat openai/gpt-4o-mini"


def test_a_model_span_falls_back_when_the_model_is_unknown():
    """Never emit a dangling ``chat `` — an unnamed model keeps the bare span name."""
    assert semconv.model_span("") == semconv.SPAN_MODEL
    assert semconv.model_span(None) == semconv.SPAN_MODEL


def test_bookkeeping_nodes_are_named_as_untraced():
    """A supervisor declares which of its nodes do no work worth a span.

    ``lookup_route``, ``resolve`` and ``safety_gate`` are pure functions — no model call, no
    tool, no I/O. Emitting a span each buries the three that matter (classify, dispatch, and
    the sub-agent) in bookkeeping.
    """
    from agentship_langgraph.templates.graph_supervisor import UNTRACED_NODES

    assert {"lookup_route", "resolve", "safety_gate"} <= UNTRACED_NODES
    assert "classify" not in UNTRACED_NODES, "classify holds the routing model call"
    assert "dispatch" not in UNTRACED_NODES, "dispatch holds the sub-agent"
