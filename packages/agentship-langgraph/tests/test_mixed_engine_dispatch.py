"""C7.3: a supervisor dispatches specialists on *different* engines through one ``run`` port.

AgentRef.run calls the specialist's public ``run`` — never an engine internal — so a supervisor can
fan out to agents on different engines and just collect their structured outputs, with no shared
graph state. The design's target is a LangGraph supervisor calling an ADK specialist; ADK lands in
P14, so this proves the same property today with an **echo** specialist and a **langgraph**
specialist side by side. When ADK arrives it swaps in unchanged.
"""

from __future__ import annotations

import agentship_langgraph.models as models_module
import pytest
from agentship.context import Caller, RunContext, RunMode
from agentship.primitives.dispatch import AgentRef, dispatch
from agentship.runtime import build_agent
from agentship.spec import AgentSpec
from langchain_core.language_models.fake_chat_models import FakeListChatModel


@pytest.fixture
def fake_model(monkeypatch):
    """Give the langgraph specialist a deterministic fake model (no network)."""
    fake = FakeListChatModel(responses=["Red, green, and blue."])
    monkeypatch.setattr(models_module, "resolve_model", lambda *a, **k: fake)
    return fake


def _ctx() -> RunContext:
    return RunContext(
        caller=Caller(user_id="alice"),
        session_id="s1",
        run_id="r1",
        agent_name="supervisor",
        mode=RunMode.INVOKE,
    )


async def test_supervisor_dispatches_across_echo_and_langgraph(fake_model):
    """Parallel dispatch to an echo agent and a langgraph agent returns both structured outputs."""
    echo = build_agent(AgentSpec(name="echoer", engine="echo"))
    lg = build_agent(AgentSpec(name="painter", engine="langgraph", model="x", prompt="p"))
    registry = {"echoer": echo, "painter": lg}

    refs = [AgentRef.resolve(name, registry) for name in ("echoer", "painter")]
    results = await dispatch("parallel", refs, "primary colors?", _ctx())

    by_name = {r["name"]: r for r in results}
    assert by_name["echoer"]["error"] is None
    assert by_name["echoer"]["output"] == {"output": "echo: primary colors?"}
    assert by_name["painter"]["error"] is None
    assert by_name["painter"]["output"] == {"output": "Red, green, and blue."}
