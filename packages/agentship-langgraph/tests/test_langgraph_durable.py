"""C4.2/C4.4/C4.5: durable run, ResumeToken minting, and resume on the LangGraph engine.

Proves the crash-durable half of Phase 02: a ``durability="checkpoint"`` agent checkpoints per node
and mints a :class:`ResumeToken`; ``engine.resume(token)`` re-hydrates from that checkpoint and
continues to an **identical** result; a non-durable agent mints no token; the engine flushes
checkpoints in ``sync`` mode via ``.ainvoke``; and a minted token round-trips through opaque
(blob-only) reconstruction.

The in-memory tests force the no-database path (shared in-process saver) by clearing
``AGENT_SESSION_STORE_URI``; a Postgres test — gated on that same var — proves resume across two
*separate* checkpointer pools (i.e. real cross-process durability).
"""

from __future__ import annotations

import os

import agentship_langgraph.models as models_module
import pytest
from agentship.context import Caller, RunContext, RunMode
from agentship.engines.base import ResumeToken
from agentship.spec import AgentSpec
from agentship_langgraph.engine import LangGraphEngine
from langchain_core.language_models.fake_chat_models import FakeListChatModel

_PG = os.environ.get("AGENT_SESSION_STORE_URI")
requires_pg = pytest.mark.skipif(_PG is None, reason="AGENT_SESSION_STORE_URI not set")


@pytest.fixture
def fake_model(monkeypatch):
    """Patch the engine's model seam with a deterministic fake chat model (no network)."""
    fake = FakeListChatModel(responses=["Red, green, and blue."])
    monkeypatch.setattr(models_module, "resolve_model", lambda *a, **k: fake)
    return fake


def _ctx(session_id: str = "thread-x", tenant: str = "default") -> RunContext:
    """A RunContext whose ``session_id`` is the checkpoint thread id."""
    return RunContext(
        caller=Caller(user_id="u1", tenant_id=tenant),
        session_id=session_id,
        run_id="r1",
        agent_name="a",
        mode=RunMode.INVOKE,
    )


def _durable_spec() -> AgentSpec:
    """A durable single-node agent spec."""
    return AgentSpec(
        name="a",
        engine="langgraph",
        model="x",
        prompt="p",
        durability="checkpoint",
    )


async def test_durable_run_mints_a_resume_token(fake_model, monkeypatch):
    """A durable run returns the answer AND a langgraph ResumeToken carrying the checkpoint."""
    monkeypatch.delenv("AGENT_SESSION_STORE_URI", raising=False)
    engine = LangGraphEngine()
    compiled = engine.build(_durable_spec())
    result = await engine.run(compiled, "hi", _ctx(session_id="t-mint"))
    assert result.output == "Red, green, and blue."
    tok = result.resume_token
    assert tok is not None and tok.engine == "langgraph"
    assert tok.blob["thread_id"] == "t-mint"
    assert tok.blob["checkpoint_id"]  # a real checkpoint id was captured
    assert tok.blob["interrupt"] is False  # the run completed


async def test_non_durable_run_mints_no_token(fake_model, monkeypatch):
    """A non-durable agent returns no ResumeToken — an engine only mints when it checkpoints."""
    monkeypatch.delenv("AGENT_SESSION_STORE_URI", raising=False)
    engine = LangGraphEngine()
    compiled = engine.build(AgentSpec(name="a", engine="langgraph", model="x", prompt="p"))
    result = await engine.run(compiled, "hi", _ctx())
    assert result.resume_token is None


async def test_resume_continues_to_identical_output_in_memory(fake_model, monkeypatch):
    """run → resume yields the identical output (the resume guarantee), in-process saver."""
    monkeypatch.delenv("AGENT_SESSION_STORE_URI", raising=False)
    engine = LangGraphEngine()
    compiled = engine.build(_durable_spec())
    ctx = _ctx(session_id="t-resume-mem")
    first = await engine.run(compiled, "hi", ctx)
    again = await engine.resume(compiled, first.resume_token, ctx)
    assert again.output == first.output


async def test_engine_flushes_checkpoints_in_sync_mode(fake_model, monkeypatch):
    """The engine flushes checkpoints in ``sync`` mode via ``.ainvoke(durability=…)``.

    Flush timing is an engine detail, not a spec field, so the engine always chooses the strongest
    crash guarantee (``sync``: a completed node's state is durable before the next node runs).
    """
    monkeypatch.delenv("AGENT_SESSION_STORE_URI", raising=False)
    from langgraph.graph.state import StateGraph

    captured: dict = {}
    real_compile = StateGraph.compile

    def spy_compile(self, **kw):
        graph = real_compile(self, **kw)
        real_ainvoke = graph.ainvoke

        async def recording_ainvoke(inp, **k):
            captured["durability"] = k.get("durability")
            return await real_ainvoke(inp, **k)

        graph.ainvoke = recording_ainvoke
        return graph

    monkeypatch.setattr(StateGraph, "compile", spy_compile)
    engine = LangGraphEngine()
    compiled = engine.build(_durable_spec())
    await engine.run(compiled, "hi", _ctx(session_id="t-mode"))
    assert captured["durability"] == "sync"


async def test_resume_token_round_trips_through_opaque_blob(fake_model, monkeypatch):
    """A token reconstructed from (engine, blob) alone — no field inspection — still resumes."""
    monkeypatch.delenv("AGENT_SESSION_STORE_URI", raising=False)
    engine = LangGraphEngine()
    compiled = engine.build(_durable_spec())
    ctx = _ctx(session_id="t-roundtrip")
    first = await engine.run(compiled, "hi", ctx)
    # Simulate P09 persistence: serialize the blob as JSONB and rebuild without inspecting it.
    tok = first.resume_token
    reconstructed = ResumeToken(engine=tok.engine, blob=dict(tok.blob))
    again = await engine.resume(compiled, reconstructed, ctx)
    assert again.output == first.output


@requires_pg
async def test_resume_across_separate_pools_postgres(fake_model):
    """Postgres durability: run and resume open *separate* pools, yet resume finds the state."""
    engine = LangGraphEngine()
    # Ensure the checkpoint tables exist (setup is otherwise gated off the hot path).
    from agentship_langgraph.durability import open_checkpointer

    async with open_checkpointer(_PG, setup=True):
        pass

    compiled = engine.build(_durable_spec())
    ctx = _ctx(session_id="t-pg-resume")
    first = await engine.run(compiled, "hi", ctx)
    assert first.resume_token.blob["checkpoint_id"]
    again = await engine.resume(compiled, first.resume_token, ctx)
    assert again.output == first.output
