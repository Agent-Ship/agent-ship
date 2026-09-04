"""A supervisor streams its ANSWER, not its internal reasoning.

The engine streams model output from every node. In a single-agent ReAct loop that is exactly
right — every token is part of the reply. In a supervisor it is not: the classify node's model
emits the routing label ("note_keeper", "billing"), which is bookkeeping, and it arrived at the
client as the first content of the answer. A real turn streamed back:

    "note_keeperI saved a note recording that Python 3.14.7 ..."

The label is glued to the front of the reply with no separator, so it reads as a typo rather
than as a leaked internal step, which makes it worse.
"""

from __future__ import annotations

import agentship_langgraph.models as models_module
from agentship.runtime import RunnableAgent
from agentship.spec import AgentSpec
from agentship_langgraph.engine import LangGraphEngine
from agentship_langgraph.templates.graph_config import GraphConfig
from agentship_langgraph.templates.graph_supervisor import SupervisorAgent
from langchain_core.language_models.fake_chat_models import FakeListChatModel

_CFG = GraphConfig.model_validate(
    {
        "classify": {"model": "x", "intents": ["billing"]},
        "routing": {
            "billing": {"specialists": ["billing_specialist"], "strategy": "single"},
            "_default": {"specialists": ["billing_specialist"], "strategy": "single"},
        },
        "conflict_resolver": {"priority": ["billing_specialist"]},
    }
)


async def test_the_routing_label_is_not_streamed_as_the_answer(monkeypatch):
    """The classify node's label never reaches the client's content stream."""
    from agentship.runtime import build_agent

    monkeypatch.setattr(
        models_module, "resolve_model", lambda *a, **k: FakeListChatModel(responses=["billing"])
    )
    specialists = {
        "billing_specialist": build_agent(
            AgentSpec(name="billing_specialist", engine="echo")
        )
    }
    spec = AgentSpec(name="triage", engine="langgraph", model="x", streaming=True)
    engine = LangGraphEngine()
    compiled = engine.build(spec, SupervisorAgent(spec, config=_CFG, specialists=specialists))
    agent = RunnableAgent(spec, engine, compiled)

    chunks: list[str] = []
    async for event in agent.stream("my invoice is wrong"):
        if event.type != "content":
            continue
        data = event.data
        chunks.append(str(data.get("content", "") if isinstance(data, dict) else data))
    streamed = "".join(chunks)

    assert not streamed.startswith("billing"), (
        f"the routing label leaked into the answer stream: {streamed[:80]!r}"
    )
