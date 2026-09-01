"""An agent remembers the conversation, without asking for crash recovery.

Session memory and checkpointing are different promises that happened to share a
mechanism, and AgentShip had fused them into one flag:

    session memory   the agent knows what was said earlier on this session_id
    durability       an interrupted run can be resumed from where it stopped

Only ``durability: checkpoint`` attached a checkpointer, so a plain agent started every
turn blank — you had to opt into a flag named for crash recovery to get the agent to
remember your last message. These pin the split: memory is unconditional, ``durability``
is only about surviving a crash.

Offline: a fake model that echoes back the history it was given, so no key and no network.
"""

from __future__ import annotations

import agentship_langgraph.models as models_module
from agentship.runtime import build_agent
from agentship.spec import AgentSpec
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field


class RecallingModel(BaseChatModel):
    """A fake model whose reply lists every human turn it was handed.

    That makes the assertion about *what the agent was given*, not about a real model's
    behaviour: if the history is threaded, turn 2's prompt still contains turn 1.
    """

    model: str = "openai/gpt-4o-mini"
    prompts: list = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        """LangChain's model-type tag; required by the base class."""
        return "recalling"

    def bind_tools(self, tools, **kwargs):
        """No tools in these tests; return self so the graph builds."""
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        """Answer with the human turns seen so far, so history is observable."""
        said = [m.content for m in messages if m.type == "human"]
        self.prompts.append(list(messages))
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=" | ".join(said)))])


async def test_a_plain_agent_remembers_the_earlier_turn(monkeypatch):
    """No ``durability`` set, yet turn 2 still sees turn 1 — memory is not opt-in."""
    monkeypatch.setattr(models_module, "resolve_model", lambda *a, **k: RecallingModel())
    agent = build_agent(AgentSpec(name="plain", engine="langgraph", template="single", model="x"))

    await agent.run("my codeword is falcon", session_id="s-mem")
    second = await agent.run("what is it?", session_id="s-mem")

    assert "my codeword is falcon" in second.output, (
        f"turn 2 did not see turn 1 — the agent started blank: {second.output!r}"
    )


async def test_a_different_session_does_not_see_the_other_conversation(monkeypatch):
    """Memory is scoped to its ``session_id``; a new session starts clean."""
    monkeypatch.setattr(models_module, "resolve_model", lambda *a, **k: RecallingModel())
    agent = build_agent(AgentSpec(name="plain", engine="langgraph", template="single", model="x"))

    await agent.run("my codeword is falcon", session_id="s-one")
    other = await agent.run("what is it?", session_id="s-two")

    assert "falcon" not in other.output, f"a separate session leaked history: {other.output!r}"
