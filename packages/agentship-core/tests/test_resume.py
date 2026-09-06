"""T5 proof: the ``ResumeToken`` type and the capability-gated ``Engine.resume`` seam.

The *interface* for durable resume lands here; a working checkpoint resume is Phase
02. This pins the seam so an engine cannot fake durability:

* :class:`ResumeToken` is a plain ``{engine, blob}`` value (``blob`` opaque to core)
  and is exported from the public surface;
* :meth:`Engine.resume` is capability-gated — on an engine that declares
  ``durability="none"`` it raises :class:`CapabilityError` (declare, don't fake);
* a token minted by a *different* engine is always rejected with
  :class:`CapabilityError`, even before durability is considered.
"""

from __future__ import annotations

import pytest
from agentship import ResumeToken as ExportedResumeToken
from agentship.context import Caller, RunContext, RunMode
from agentship.engines.base import Engine, EngineCapabilities, Result, ResumeToken
from agentship.errors import CapabilityError


def _ctx() -> RunContext:
    """A minimal RunContext for driving resume."""
    return RunContext(
        caller=Caller(user_id="u1"),
        session_id="s1",
        run_id="r1",
        agent_name="a",
        mode=RunMode.INVOKE,
    )


class _NoDurabilityEngine(Engine):
    """An engine that honestly declares no durability — resume must be rejected."""

    name = "no_durability"
    capabilities = EngineCapabilities(durability="none")

    def build(self, spec, authored=None):
        """No compilation needed for the seam test."""
        return spec

    async def run(self, compiled, text, ctx):
        """Echo the text — enough to be a concrete engine."""
        return Result(output=text)


class _DurableEngine(Engine):
    """An engine declaring checkpoint durability (used to test the wrong-engine guard)."""

    name = "durable_probe"
    capabilities = EngineCapabilities(durability="checkpoint")

    def build(self, spec, authored=None):
        """No compilation needed for the seam test."""
        return spec

    async def run(self, compiled, text, ctx):
        """Echo the text — enough to be a concrete engine."""
        return Result(output=text)


def test_resume_token_shape_and_export():
    """ResumeToken carries {engine, blob} and is re-exported from the public surface."""
    token = ResumeToken(engine="langgraph", blob={"thread_id": "t1"})
    assert token.engine == "langgraph"
    assert token.blob == {"thread_id": "t1"}
    assert ExportedResumeToken is ResumeToken


async def test_resume_on_non_durable_engine_raises_capability_error():
    """An engine with durability='none' rejects resume (declare, don't fake).

    Non-vacuous: an engine that *declared* durability would not raise here, so the
    error is caused specifically by the honest durability='none' declaration.
    """
    engine = _NoDurabilityEngine()
    token = ResumeToken(engine="no_durability", blob={})
    with pytest.raises(CapabilityError):
        await engine.resume(engine.build(None), token, _ctx())


async def test_resume_rejects_a_wrong_engine_token():
    """A token minted by another engine is rejected even on a durable engine.

    The durable engine declares durability, so the durability gate would pass — the
    rejection is caused solely by the token.engine mismatch, making this non-vacuous
    (a matching-engine token would get past this guard).
    """
    engine = _DurableEngine()
    foreign = ResumeToken(engine="some_other_engine", blob={})
    with pytest.raises(CapabilityError):
        await engine.resume(engine.build(None), foreign, _ctx())


# ---- RunnableAgent.resume — resume as a first-class public operation ---------------------------


class _PausingEngine(Engine):
    """A durable engine whose first run pauses and whose resume returns the decision it got.

    Enough to prove the public ``resume`` threads a caller, a session and a resume value
    through to the engine — without needing a real checkpointer.
    """

    name = "pausing"
    capabilities = EngineCapabilities(durability="checkpoint")

    def build(self, spec, authored=None):
        """Nothing to compile — the test drives run/resume directly."""
        return object()

    async def run(self, compiled, text, ctx):
        """Pause immediately, handing back a token the caller is expected to resume with."""
        return Result(
            output=None, resume_token=ResumeToken(engine=self.name, blob={"t": ctx.session_id})
        )

    async def resume(self, compiled, token, ctx, *, resume_value=None):
        """Report what the human decided, plus the session the resume ran on."""
        return Result(output=f"decided={resume_value} on={ctx.session_id}")


async def test_runnable_agent_exposes_resume_as_a_public_operation():
    """``RunnableAgent.resume`` continues a paused run — no reaching into engine internals.

    Before this existed, a caller holding a resume token had to build a RunContext by hand
    and call ``agent.engine.resume(agent.compiled, ...)``. That is why the HTTP service had
    no resume endpoint: there was no public operation to expose. ``run``/``stream``/``resume``
    are now symmetric.
    """
    from agentship.engines.base import ENGINES
    from agentship.runtime import build_agent
    from agentship.spec import AgentSpec

    ENGINES.register("pausing", _PausingEngine)
    try:
        agent = build_agent(AgentSpec(name="p", engine="pausing", durability="checkpoint"))
        paused = await agent.run("do it", session_id="s-42")
        assert paused.output is None and paused.resume_token is not None

        done = await agent.resume(
            paused.resume_token, resume_value={"approved": True}, session_id="s-42"
        )
        assert done.output == "decided={'approved': True} on=s-42"
    finally:
        ENGINES._providers.pop("pausing", None)
