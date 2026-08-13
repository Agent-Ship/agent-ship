"""The LangGraph engine is durable (Phase 02 §C4) — and its ``resume`` guards are honest.

The engine now declares ``durability="checkpoint"`` and implements a real ``resume``. These tests
pin the guards that keep resume safe: a token minted by a *different* engine is refused
(``CapabilityError``), and a token missing its ``thread_id`` cannot be resumed (``ResumeError``).
The happy-path resume (re-hydrate + continue to an identical result) is proven in
``test_langgraph_durable.py``.
"""

from __future__ import annotations

import pytest
from agentship.context import Caller, RunContext, RunMode
from agentship.engines.base import ResumeToken
from agentship.errors import CapabilityError, ResumeError
from agentship_langgraph.engine import LangGraphEngine


def _ctx() -> RunContext:
    """A minimal RunContext for driving resume."""
    return RunContext(
        caller=Caller(user_id="u1"),
        session_id="s1",
        run_id="r1",
        agent_name="a",
        mode=RunMode.INVOKE,
    )


def test_langgraph_declares_checkpoint_durability():
    """The engine declares durability='checkpoint' now that a real checkpointer is wired."""
    assert LangGraphEngine.capabilities.durability == "checkpoint"


async def test_resume_rejects_a_wrong_engine_token():
    """A token minted by another engine is refused before any state work — CapabilityError."""
    engine = LangGraphEngine()
    foreign = ResumeToken(engine="some_other_engine", blob={"thread_id": "t1"})
    with pytest.raises(CapabilityError):
        await engine.resume(object(), foreign, _ctx())


async def test_resume_rejects_a_token_without_thread_id():
    """A same-engine token that carries no thread_id cannot be resumed — ResumeError."""
    engine = LangGraphEngine()
    token = ResumeToken(engine="langgraph", blob={})
    with pytest.raises(ResumeError):
        await engine.resume(object(), token, _ctx())
