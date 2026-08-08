"""T5 proof (langgraph side): the engine is honest — resume is gated off until P02.

The LangGraph engine declares ``durability="none"`` today (no checkpointer is wired
yet — that lands in Phase 02), so its inherited :meth:`Engine.resume` must raise a
clean :class:`CapabilityError` rather than pretend to resume. This guards against a
premature over-claim: the day the engine flips to ``durability="checkpoint"`` and
implements a real resume, this test is updated alongside it.
"""

from __future__ import annotations

import pytest
from agentship.context import Caller, RunContext, RunMode
from agentship.engines.base import ResumeToken
from agentship.errors import CapabilityError
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


def test_langgraph_declares_no_durability_yet():
    """The engine honestly declares durability='none' (the checkpointer is P02)."""
    assert LangGraphEngine.capabilities.durability == "none"


async def test_langgraph_resume_raises_until_phase_02():
    """resume on the (non-durable) LangGraph engine raises CapabilityError, not a fake result.

    Non-vacuous: it fails if the engine ever silently pretends to resume without a
    checkpointer — the honest failure is the whole point until Phase 02 lands one.
    """
    engine = LangGraphEngine()
    token = ResumeToken(engine="langgraph", blob={"thread_id": "t1"})
    with pytest.raises(CapabilityError):
        await engine.resume(object(), token, _ctx())
