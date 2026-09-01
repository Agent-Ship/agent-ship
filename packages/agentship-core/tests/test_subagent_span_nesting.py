"""A supervisor and its sub-agents belong in ONE trace, not four.

When a supervisor dispatches three specialists, everything that happened should appear under
one root: the supervisor's span, each member's span beneath it, and each member's model and
tool spans beneath that. Otherwise the trace answers "the supervisor ran" and nothing about
what the members actually did — which is the whole reason to trace a multi-agent system.

Today each member opens its OWN root span through its OWN observer (a no-op unless that
member's YAML happens to declare an observability block), so a supervisor turn produces four
disconnected traces. These tests pin the tree.
"""

from __future__ import annotations

from agentship.context import Caller, RunContext, RunMode
from agentship.observability import SpanKind, current_observer
from agentship.observability.recorder import RecordingObserver
from agentship.primitives.dispatch import AgentRef, dispatch
from agentship.runtime import build_agent
from agentship.spec import AgentSpec


def _ctx() -> RunContext:
    """A minimal context for driving a dispatch."""
    return RunContext(
        caller=Caller(user_id="u"),
        session_id="s",
        run_id="r",
        agent_name="supervisor",
        mode=RunMode.INVOKE,
    )


def _names(node) -> list[str]:
    """Flatten a recorded span tree into names, so a test can assert what nested where."""
    out = [node.name]
    for child in node.children:
        out.extend(_names(child))
    return out


async def test_a_dispatched_member_nests_under_the_supervisors_span():
    """A member's span is a CHILD of the supervisor's, not a second root.

    Asserts on the tree, not merely that spans exist: a flat list of four roots would pass a
    "spans were emitted" check while being exactly the broken thing.
    """
    observer = RecordingObserver()
    member = build_agent(AgentSpec(name="billing", engine="echo"))

    # Mirror what a supervisor's own run does: publish its observer as the ambient one and
    # open its root span. The member is dispatched inside that.
    token = current_observer.set(observer)
    try:
        with observer.span("agent", SpanKind.AGENT, {}):
            await dispatch("single", [AgentRef("billing", member)], "a question", _ctx())
    finally:
        current_observer.reset(token)

    roots = observer.roots
    assert len(roots) == 1, (
        f"expected ONE trace, got {len(roots)} disconnected roots — the member did not "
        f"nest: {[_names(r) for r in roots]}"
    )
    assert len(roots[0].children) >= 1, (
        f"the supervisor span has no children; the member's work is invisible: {_names(roots[0])}"
    )
