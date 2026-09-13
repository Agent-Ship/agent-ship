"""An agent remembers what was said a moment ago — the claim P03 makes.

The stub model REPEATS BACK every human message it was given, so an assertion about the
second turn seeing the first is an assertion about what reached the model, not about a
provider choosing to recall something.
"""

from __future__ import annotations

import pytest
from agentship.runtime import build_agent
from agentship.spec import AgentSpec
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage


class _EchoesHistory(FakeMessagesListChatModel):
    """A model whose reply is the conversation it was handed, joined together.

    That makes the history visible in the output: if the graph replaced its messages instead
    of accumulating them, the reply contains only the newest turn and the test fails for the
    right reason.
    """

    def __init__(self) -> None:
        """No canned responses — every reply is built from the input."""
        super().__init__(responses=[AIMessage(content="unused")])

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        """Return the human turns seen, newest last."""
        from langchain_core.outputs import ChatGeneration, ChatResult

        said = [m.content for m in messages if m.__class__.__name__ == "HumanMessage"]
        return ChatResult(
            generations=[ChatGeneration(message=AIMessage(content="saw: " + " | ".join(said)))]
        )


@pytest.fixture
def echoing_model(monkeypatch):
    """Point the engine's model seam at the history-echoing stub."""
    import agentship_langgraph.models as models_module

    model = _EchoesHistory()
    monkeypatch.setattr(models_module, "resolve_model", lambda *a, **k: model)
    return model


def _agent(name: str = "rememberer", streaming: bool = False):
    """Build an agent over whatever the model seam currently resolves to.

    The model NAME must name a provider the engine declares — the capability gate checks that
    before anything runs — even though the seam replaces the model itself.
    """
    return build_agent(
        AgentSpec(
            name=name,
            engine="langgraph",
            model="openai/gpt-4o-mini",
            prompt="Be brief.",
            streaming=streaming,
        )
    )


@pytest.mark.asyncio
async def test_a_second_turn_sees_the_first(echoing_model) -> None:
    """The graph's message channel accumulates instead of being replaced each turn.

    ``_AgentState.messages`` was a plain ``list`` with no reducer, so every turn overwrote the
    conversation and the graph began from nothing — an agent told a name answered "I don't
    have access to personal information" one message later, which reads as a model limitation
    rather than as a conversation that was thrown away. A checkpointer cannot rescue a channel
    that overwrites itself: it stores the conversation faithfully and then never reads it.
    """
    agent = _agent()
    session = "memory-across-turns"

    await agent.run("My name is Harshul.", session_id=session)
    second = await agent.run("What is my name?", session_id=session)

    assert "My name is Harshul." in str(second.output), "the second turn must see the first"


@pytest.mark.asyncio
async def test_separate_sessions_do_not_leak_into_each_other(echoing_model) -> None:
    """Memory is per session id: two conversations must not read each other's history."""
    agent = _agent()

    await agent.run("My name is Harshul.", session_id="conversation-a")
    other = await agent.run("Who am I?", session_id="conversation-b")

    assert "Harshul" not in str(other.output), "a different session is a different conversation"


@pytest.mark.asyncio
async def test_a_streamed_turn_remembers_too(echoing_model) -> None:
    """Streaming is a delivery mode, not a different kind of conversation.

    The streaming path ran on a graph with no thread bound at all, so a voice caller — who only
    ever streams — had no memory while a text caller did.
    """
    agent = _agent("streamer", streaming=True)
    session = "streamed-memory"

    async for _ in agent.stream("My name is Harshul.", session_id=session):
        pass
    heard = "".join(
        [
            e.data
            async for e in agent.stream("What is my name?", session_id=session)
            if e.type == "content" and isinstance(e.data, str)
        ]
    )

    assert "My name is Harshul." in heard, "a streamed second turn must see the first"
