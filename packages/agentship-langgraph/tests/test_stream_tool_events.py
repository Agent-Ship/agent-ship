"""A streamed turn must say WHICH tool it is calling, not just dribble out text.

The wire contract declares eight event types (``session|token|content|tool_call|
tool_result|guard|done|error``) but the engine only ever emitted ``content`` and ``done``.
So a client streaming a tool-using agent saw silence while the agent worked, then an
answer — no "calling weather", no result coming back. That is the difference between a
black box and something you can build against.

Offline: a scripted fake model drives a real ReAct loop, so no key and no network.
"""

from __future__ import annotations

import agentship_langgraph.models as models_module
from agentship.runtime import build_agent
from agentship.spec import AgentSpec
from agentship.tools import Tool
from langchain_core.messages import AIMessage
from pydantic import BaseModel
from test_tool_execution import ScriptedModel


class _CityArgs(BaseModel):
    """Arguments of the ``weather`` tool — one city, so the model can call it."""

    city: str


def _weather(city: str) -> str:
    """Return a fixed forecast; the assertions are about events, not the tool."""
    return f"sunny in {city}"


#: Referenced from a spec as ``test_stream_tool_events:weather`` so the call travels the
#: real resolve → bind → tool-node path rather than a hand-built wrapper.
weather = Tool("weather", "look up the weather", _weather, args_schema=_CityArgs)

#: First reply asks for the tool; second answers. Mirrors a real two-hop ReAct turn.
_SCRIPT = [
    AIMessage(
        content="",
        tool_calls=[{"name": "weather", "args": {"city": "Oslo"}, "id": "call_1"}],
    ),
    AIMessage(content="It is sunny in Oslo."),
]


async def _stream_types(monkeypatch) -> list[str]:
    """Run one streamed turn on the scripted model and return the event types it emitted."""
    monkeypatch.setattr(
        models_module, "resolve_model", lambda *a, **k: ScriptedModel(script=_SCRIPT)
    )
    agent = build_agent(
        AgentSpec(
            name="w",
            engine="langgraph",
            template="single",
            model="x",
            streaming=True,
            tools=["test_stream_tool_events:weather"],
        )
    )
    return [event.type async for event in agent.stream("weather in Oslo?")]


async def test_stream_announces_the_tool_it_is_calling(monkeypatch):
    """A streamed turn emits ``tool_call`` — otherwise a watching user sees only silence."""
    kinds = await _stream_types(monkeypatch)
    assert "tool_call" in kinds, f"no tool_call event emitted; got {kinds}"


async def test_stream_reports_the_tool_result(monkeypatch):
    """A streamed turn emits ``tool_result`` so a client can show what came back."""
    kinds = await _stream_types(monkeypatch)
    assert "tool_result" in kinds, f"no tool_result event emitted; got {kinds}"
