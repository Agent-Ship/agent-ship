"""T3 proof: the LangGraph engine, offline, with a fake chat model injected.

No network: these monkeypatch :func:`agentship.models.resolve_model` so the engine
compiles a graph over a deterministic ``FakeListChatModel``. They assert the real
mechanism — ``run`` returns the model's answer through the compiled graph;
``stream`` yields >=1 content chunk then a terminal ``done``; and the capability
gate rejects ``output:``/``members:`` on this engine (it declares neither).
"""

from __future__ import annotations

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

import agentship.models as models_module
from agentship.errors import CapabilityError
from agentship.runtime import build_agent
from agentship.spec import AgentSpec, MemberSpec


@pytest.fixture
def fake_model(monkeypatch):
    """Inject a deterministic fake chat model in place of the real LiteLLM one.

    Patches :func:`agentship.models.resolve_model` (the engine's model seam) so
    ``build_agent`` compiles its graph over a ``FakeListChatModel`` — proving the
    wiring end to end with zero network calls.
    """
    fake = FakeListChatModel(responses=["Red, green, and blue."])
    monkeypatch.setattr(models_module, "resolve_model", lambda *a, **k: fake)
    return fake


async def test_run_returns_the_models_answer_through_the_graph(fake_model):
    """run() returns the fake model's answer, routed through the compiled graph."""
    agent = build_agent(
        AgentSpec(name="a", engine="langgraph", model="x", prompt="You are helpful.")
    )
    result = await agent.run("Name three primary colors.")
    assert result.output == "Red, green, and blue."


async def test_stream_yields_content_chunks_then_done(fake_model):
    """stream() yields >=1 content chunk (token-level) then exactly one terminal done."""
    agent = build_agent(
        AgentSpec(name="a", engine="langgraph", model="x", prompt="p", streaming=True)
    )
    events = [e async for e in agent.stream("hi")]

    content = [e for e in events if e.type == "content"]
    assert len(content) >= 1
    # Token-level streaming: the answer arrives across multiple content chunks.
    assert len(content) > 1
    joined = "".join(e.data for e in content)
    assert joined == "Red, green, and blue."

    assert events[-1].type == "done"
    assert sum(1 for e in events if e.type == "done") == 1


def test_output_schema_rejected_by_capability_gate(fake_model):
    """This engine does not declare structured_output — an output: spec fails fast."""
    with pytest.raises(CapabilityError) as exc:
        build_agent(AgentSpec(name="a", engine="langgraph", model="x", output="MyModel"))
    assert "structured output" in str(exc.value).lower()


def test_members_rejected_by_capability_gate(fake_model):
    """This engine is not multi-agent — a members: spec fails fast, not silently dropped."""
    spec = AgentSpec(
        name="team", engine="langgraph", model="x", members=[MemberSpec(name="m1")]
    )
    with pytest.raises(CapabilityError) as exc:
        build_agent(spec)
    assert "multi-agent" in str(exc.value).lower()
