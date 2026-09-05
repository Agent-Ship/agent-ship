"""Supported test helpers for the LangGraph engine — the vendor-side conformance seam.

The vendor-neutral conformance catalogue (:mod:`agentship.conformance`) must not
import langchain/langgraph/litellm, so anything that needs a vendor type lives
here, in the engine package, and is passed *into* the grid:

* :func:`offline` — a context manager that swaps the engine's model seam for a
  deterministic fake, so a positive conformance cell drives a real LangGraph agent
  with zero network calls. ``agentship verify`` (and the conformance test tree)
  hand this to :func:`agentship.conformance.run_capability_grid` as its ``offline``
  provider.
* :func:`build_hitl_agent` — the ``code:`` factory the ``hitl`` conformance cell
  builds through. It returns a durable confirm/write agent that pauses at
  ``interrupt()`` before a side effect, so the cell can prove the engine genuinely
  surfaces the HITL interrupt rather than running straight through.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, ExitStack, contextmanager, nullcontext

from agentship.spec import AgentSpec
from langchain_core.messages import AIMessage
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from typing_extensions import TypedDict

from agentship_langgraph.agent import LangGraphAgent


@contextmanager
def _langgraph_offline() -> Iterator[None]:
    """Patch the LangGraph engine's model seam to a deterministic fake, for one cell.

    Replaces :func:`agentship_langgraph.models.resolve_model` with a factory that
    returns a token-streaming ``FakeListChatModel``, so the engine compiles and
    drives its graph with zero network calls. Restored on exit.
    """
    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    import agentship_langgraph.models as models_module

    original = models_module.resolve_model
    fake = FakeListChatModel(responses=["conformance ok, streamed in pieces."])
    models_module.resolve_model = lambda *args, **kwargs: fake  # type: ignore[assignment]
    try:
        yield
    finally:
        models_module.resolve_model = original  # type: ignore[assignment]


#: Maps an engine name to a zero-arg context manager that makes its cells offline.
#: An engine absent from this map runs with no patching (see :func:`offline`). This
#: is the only edit needed to bring a new model-backed engine into the matrix.
OFFLINE_HARNESSES: dict[str, Callable[[], AbstractContextManager[None]]] = {
    "langgraph": _langgraph_offline,
}


@contextmanager
def offline(engine_name: str) -> Iterator[None]:
    """Enter the offline harness for ``engine_name`` (a no-op if it needs none).

    Model-free engines (``echo``) have no entry and run unpatched; model-backed
    engines get their model seam swapped for a fake so every positive cell stays
    fully offline. Passed as the ``offline`` provider to
    :func:`agentship.conformance.run_capability_grid`.
    """
    harness = OFFLINE_HARNESSES.get(engine_name)
    with ExitStack() as stack:
        stack.enter_context(harness() if harness is not None else nullcontext())
        yield


_HITL_PAYLOAD = {"question": "Send the email?", "tool": "send_email"}


class _HitlState(TypedDict):
    """The confirm graph's state: the running messages plus the human's decision."""

    messages: list
    approved: bool


class _ConfirmAgent(LangGraphAgent):
    """A durable graph that pauses to confirm before a 'write', then writes or skips.

    Mirrors the HITL contract proven in ``tests/test_langgraph_hitl.py``: the
    ``confirm`` node calls ``interrupt(payload)`` to ask for approval before a side
    effect, so the engine surfaces a paused :class:`~agentship.engines.base.Result`
    (interrupt payload set, no output, interrupt-flagged resume token). The
    ``hitl`` conformance cell builds this and asserts the pause is surfaced.
    """

    def build_graph(self, model, tools) -> StateGraph:
        """Wire ``confirm → write``: confirm pauses at ``interrupt``, write acts on the decision."""

        def confirm(state: _HitlState) -> dict:
            """Pause for human approval, recording the decision once resumed."""
            decision = interrupt(_HITL_PAYLOAD)
            return {"approved": bool(decision and decision.get("approved"))}

        def write(state: _HitlState) -> dict:
            """Perform (or skip) the side effect according to the approval decision."""
            msg = "email sent" if state["approved"] else "email NOT sent"
            return {"messages": [*state["messages"], AIMessage(content=msg)]}

        g = StateGraph(_HitlState)
        g.add_node("confirm", confirm)
        g.add_node("write", write)
        g.add_edge(START, "confirm")
        g.add_edge("confirm", "write")
        g.add_edge("write", END)
        return g


def build_hitl_agent() -> _ConfirmAgent:
    """A ``code:`` factory returning the durable confirm/write HITL agent.

    Referenced by the ``hitl`` conformance cell as
    ``agentship_langgraph.testing:build_hitl_agent``. The spec declares
    ``durability="checkpoint"`` because a HITL interrupt is a paused durable run.
    """
    return _ConfirmAgent(
        AgentSpec(name="confirm", engine="langgraph", model="x", durability="checkpoint")
    )


__all__ = ["OFFLINE_HARNESSES", "build_hitl_agent", "offline"]
