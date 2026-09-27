"""The LangGraph engine is durable (Phase 02 §C4) — and its ``resume`` guards are honest.

The engine now declares ``durability="checkpoint"`` and implements a real ``resume``. These tests
pin the guards that keep resume safe: a token minted by a *different* engine is refused
(``CapabilityError``), and the conversation resumed is always the caller's own — decided by their
tenant and session, never by the token. The happy-path resume (re-hydrate + continue to an
identical result) is proven in ``test_langgraph_durable.py``.
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


@pytest.fixture
def fake_model(monkeypatch):
    """A keyless model with a fixed reply, so a durable run can checkpoint without a provider."""
    import agentship_langgraph.models as models_module
    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    fake = FakeListChatModel(responses=["the answer"])
    monkeypatch.setattr(models_module, "resolve_model", lambda *a, **k: fake)
    monkeypatch.delenv("AGENT_SESSION_STORE_URI", raising=False)


def _durable():
    from agentship.spec import AgentSpec

    engine = LangGraphEngine()
    return engine, engine.build(
        AgentSpec(name="a", engine="langgraph", model="x", prompt="p", durability="checkpoint")
    )


def _as(tenant: str, session: str) -> RunContext:
    return RunContext(
        caller=Caller(user_id="u1", tenant_id=tenant),
        session_id=session,
        run_id="r1",
        agent_name="a",
        mode=RunMode.INVOKE,
    )


async def test_a_token_without_thread_id_resumes_the_callers_session(fake_model):
    """The session identifies the run; a token with no thread id is enough to resume it.

    A crashed turn never returned, so the client never received the token's thread id. Resume
    has to work from the session the client already knows.
    """
    engine, compiled = _durable()
    first = await engine.run(compiled, "hi", _as("acme", "s-bare"))

    again = await engine.resume(
        compiled, ResumeToken(engine="langgraph", blob={}), _as("acme", "s-bare")
    )

    assert again.output == first.output == "the answer"


async def test_resume_of_a_session_with_no_checkpoint_is_a_resume_error(fake_model):
    """Nothing ran in this session, so there is nothing to continue — ResumeError, not a new run."""
    engine, compiled = _durable()
    with pytest.raises(ResumeError, match="no checkpoint"):
        await engine.resume(
            compiled, ResumeToken(engine="langgraph", blob={}), _as("acme", "never-ran")
        )


async def test_a_token_cannot_carry_a_caller_into_another_tenants_conversation(fake_model):
    """A token naming tenant B's thread, presented by tenant A, is refused.

    The token's thread id is plainly ``tenant/agent/session`` and is not signed. Before, resume
    used it as-is, so tenant A could type tenant B's id into a token and continue — or read —
    B's conversation. The negative control shows the same token works for its real owner, so the
    refusal is caused by the tenant mismatch and nothing else.
    """
    engine, compiled = _durable()
    victim = await engine.run(compiled, "secret", _as("tenant-b", "chat-1"))
    stolen = victim.resume_token
    assert stolen.blob["thread_id"].startswith("tenant-b/")

    with pytest.raises(ResumeError, match="different conversation"):
        await engine.resume(compiled, stolen, _as("tenant-a", "chat-1"))

    owner = await engine.resume(compiled, stolen, _as("tenant-b", "chat-1"))
    assert owner.output == victim.output


async def test_a_token_from_another_session_is_refused(fake_model):
    """Same tenant, other session: the token does not redirect the resume either."""
    engine, compiled = _durable()
    other = await engine.run(compiled, "hi", _as("acme", "s-other"))
    await engine.run(compiled, "hi", _as("acme", "s-mine"))

    with pytest.raises(ResumeError, match="different conversation"):
        await engine.resume(compiled, other.resume_token, _as("acme", "s-mine"))
