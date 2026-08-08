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
from agentship.context import Caller, RunContext, RunMode
from agentship.engines.base import Engine, EngineCapabilities, Result, ResumeToken
from agentship.errors import CapabilityError

from agentship import ResumeToken as ExportedResumeToken


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
