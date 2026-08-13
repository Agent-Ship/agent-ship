"""C5.2/C5.3: HITL — a run pauses at ``interrupt()`` and resumes with the human's decision.

A durable graph node calls ``interrupt(payload)`` to ask for confirmation before a side effect.
The engine surfaces that as a :class:`Result` with ``interrupt`` set (the payload), ``output`` None,
and a resume token marked ``interrupt=True``. ``engine.resume(token, resume_value=…)`` feeds the
decision back: **approve** → the write runs; **reject** → the write is skipped. Proven in-process
against the in-memory saver.
"""

from __future__ import annotations

import agentship_langgraph.models as models_module
import pytest
from agentship.context import Caller, RunContext, RunMode
from agentship.runtime import build_agent
from agentship.spec import AgentSpec
from agentship_langgraph import LangGraphAgent
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from typing_extensions import TypedDict

_PAYLOAD = {"question": "Send the email?", "tool": "send_email"}


class _HitlState(TypedDict):
    messages: list
    approved: bool


class ConfirmAgent(LangGraphAgent):
    """A graph that pauses to confirm before a 'write', then writes or skips per the decision."""

    def build_graph(self, model, tools) -> StateGraph:
        def confirm(state: _HitlState) -> dict:
            decision = interrupt(_PAYLOAD)
            return {"approved": bool(decision and decision.get("approved"))}

        def write(state: _HitlState) -> dict:
            msg = "email sent" if state["approved"] else "email NOT sent"
            return {"messages": [*state["messages"], AIMessage(content=msg)]}

        g = StateGraph(_HitlState)
        g.add_node("confirm", confirm)
        g.add_node("write", write)
        g.add_edge(START, "confirm")
        g.add_edge("confirm", "write")
        g.add_edge("write", END)
        return g


def build_confirm_agent() -> ConfirmAgent:
    """A ``code:`` factory returning the durable confirm/write agent."""
    return ConfirmAgent(
        AgentSpec(name="confirm", engine="langgraph", model="x", durability="checkpoint")
    )


CODE_REF = f"{__name__}:build_confirm_agent"


@pytest.fixture
def fake_model(monkeypatch):
    """The confirm graph never calls the model, but build() still resolves one."""
    fake = FakeListChatModel(responses=["x"])
    monkeypatch.setattr(models_module, "resolve_model", lambda *a, **k: fake)


def _ctx(session_id: str) -> RunContext:
    return RunContext(
        caller=Caller(user_id="alice"),
        session_id=session_id,
        run_id="r1",
        agent_name="confirm",
        mode=RunMode.INVOKE,
    )


def _built(monkeypatch):
    """Build the durable confirm agent, forcing the in-memory saver (no DB)."""
    monkeypatch.delenv("AGENT_SESSION_STORE_URI", raising=False)
    return build_agent(AgentSpec(name="confirm", engine="langgraph", code=CODE_REF))


async def test_run_pauses_at_interrupt_with_payload_and_token(fake_model, monkeypatch):
    """The run stops at the confirm node: output None, interrupt payload set, token flagged."""
    built = _built(monkeypatch)
    result = await built.engine.run(built.compiled, "send it", _ctx("hitl-1"))
    assert result.output is None
    assert result.interrupt == _PAYLOAD
    assert result.resume_token is not None and result.resume_token.blob["interrupt"] is True


async def test_resume_approve_runs_the_write(fake_model, monkeypatch):
    """Resuming with approve lets the write node run — the email is sent."""
    built = _built(monkeypatch)
    ctx = _ctx("hitl-approve")
    paused = await built.engine.run(built.compiled, "send it", ctx)
    done = await built.engine.resume(
        built.compiled, paused.resume_token, ctx, resume_value={"approved": True}
    )
    assert done.output == "email sent"
    assert done.interrupt is None


async def test_resume_reject_skips_the_write(fake_model, monkeypatch):
    """Resuming with reject skips the side effect — the email is NOT sent, no crash."""
    built = _built(monkeypatch)
    ctx = _ctx("hitl-reject")
    paused = await built.engine.run(built.compiled, "send it", ctx)
    done = await built.engine.resume(
        built.compiled, paused.resume_token, ctx, resume_value={"approved": False}
    )
    assert done.output == "email NOT sent"
