"""What makes two side-effecting tool calls "the same call" — through the real ReAct tool loop.

A key too coarse is as dangerous as no key: the second call is answered from the ledger and the
write silently never happens. Before this change the key was ``(session_id, "tool", name, args)``,
which got two cases wrong:

- two tenants that both used session ``"chat-1"`` shared entries, so the second tenant was handed
  the first tenant's receipt and its own refund never ran;
- the same request in a LATER turn of one conversation looked like a replay and was skipped.

Each test runs a whole turn — model → tool node → model — with a scripted model, so the key is
built from what LangGraph and LangChain actually pass, not from hand-set values.
"""

from __future__ import annotations

import agentship_langgraph.models as models_module
import pytest
from agentship.context import Caller
from agentship.runtime import build_agent
from agentship.spec import AgentSpec
from agentship.tools import Tool
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import BaseModel, Field

#: Every firing of the refund effect, as (tenant-visible label). The assertion target.
FIRED: list[str] = []


class _RefundArgs(BaseModel):
    order_id: str


def _refund(order_id: str) -> str:
    FIRED.append(order_id)
    return f"refund #{len(FIRED)} for {order_id}"


#: Referenced from specs as ``test_tool_idempotency_keys:refund``.
refund = Tool("refund", "refunds an order", _refund, args_schema=_RefundArgs, side_effecting=True)


class _RefundModel(BaseChatModel):
    """Asks for the scripted tool calls, then answers once a tool result is in the history.

    ``calls`` are ``(order_id, tool_call_id)`` pairs. A fixed id across turns is deliberate: some
    providers do reuse ids, and the key must not depend on them being unique.
    """

    calls: list = Field(default_factory=lambda: [("42", "call_1")])
    model: str = "openai/gpt-4o-mini"

    @property
    def _llm_type(self) -> str:
        return "refund-script"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        if isinstance(messages[-1], ToolMessage):
            reply = AIMessage(content=f"done: {messages[-1].content}")
        else:
            reply = AIMessage(
                content="",
                tool_calls=[
                    {"name": "refund", "args": {"order_id": order}, "id": call_id}
                    for order, call_id in self.calls
                ],
            )
        return ChatResult(generations=[ChatGeneration(message=reply)])


@pytest.fixture
def refund_agent(monkeypatch):
    """A durable agent with the refund tool, on a scripted model, in-memory store."""
    FIRED.clear()
    model = _RefundModel()
    monkeypatch.setattr(models_module, "resolve_model", lambda *a, **k: model)
    monkeypatch.delenv("AGENT_SESSION_STORE_URI", raising=False)
    spec = AgentSpec(
        name="payments",
        engine="langgraph",
        template="single",
        model="x",
        prompt="Refund what is asked.",
        tools=["test_tool_idempotency_keys:refund"],
        durability="checkpoint",
    )
    return build_agent(spec), model


async def test_two_tenants_with_the_same_session_id_each_get_their_own_write(refund_agent):
    """Same session id, same tool, same args, different tenants → the effect fires for EACH.

    Negative control built in: both calls are otherwise identical, so the only thing keeping the
    second one from being served the first one's receipt is the tenant in the key.
    """
    agent, _ = refund_agent
    a = await agent.run(
        "refund 42", caller=Caller(user_id="u", tenant_id="acme"), session_id="chat-1"
    )
    b = await agent.run(
        "refund 42", caller=Caller(user_id="u", tenant_id="globex"), session_id="chat-1"
    )

    assert FIRED == ["42", "42"], "one tenant's refund was answered from the other's ledger entry"
    assert "refund #1" in a.output and "refund #2" in b.output


async def test_the_same_request_in_a_later_turn_is_a_new_write(refund_agent):
    """Turn 2 asks for the same refund with the same (reused) call id → it fires again.

    The user asked twice; they get two refunds. Only a RESUME of the same turn may replay.
    """
    agent, _ = refund_agent
    caller = Caller(user_id="u", tenant_id="acme")
    await agent.run("refund 42", caller=caller, session_id="s-twice")
    await agent.run("refund 42 again", caller=caller, session_id="s-twice")

    assert FIRED == ["42", "42"]


async def test_two_identical_calls_in_one_step_are_two_writes(refund_agent):
    """The model asks for the same refund twice in one message → two distinct calls, two writes."""
    agent, model = refund_agent
    model.calls = [("42", "call_a"), ("42", "call_b")]
    await agent.run("refund 42 twice", caller=Caller(user_id="u"), session_id="s-pair")

    assert FIRED == ["42", "42"]


async def test_different_args_are_different_writes_in_one_step(refund_agent):
    """Sanity: two orders in one step are two writes — the key still includes the arguments."""
    agent, model = refund_agent
    model.calls = [("42", "call_a"), ("43", "call_b")]
    await agent.run("refund both", caller=Caller(user_id="u"), session_id="s-two-orders")

    assert sorted(FIRED) == ["42", "43"]


async def test_a_turn_writes_to_postgres_when_the_checkpoints_are_there(
    refund_agent, postgres_uri, monkeypatch
):
    """With a store configured the ledger row lands in Postgres — not in process memory.

    The in-memory ledger is checked too: it must NOT hold the entry, or a restart would still
    lose it and this test would pass for the wrong reason.
    """
    import uuid

    from agentship.primitives.ledger import LEDGER_TABLE, MEMORY_LEDGER
    from psycopg import AsyncConnection

    monkeypatch.setenv("AGENT_SESSION_STORE_URI", postgres_uri)
    agent, model = refund_agent
    # The database outlives the test run, so everything this test looks for is unique to it.
    order = f"order-{uuid.uuid4().hex[:8]}"
    model.calls = [(order, "call_1")]
    before = dict(MEMORY_LEDGER._entries)

    await agent.run(
        "refund it", caller=Caller(user_id="u", tenant_id="pg-t"), session_id=f"s-{order}"
    )

    async with await AsyncConnection.connect(postgres_uri) as conn:
        cur = await conn.execute(
            f"SELECT status, result FROM {LEDGER_TABLE} WHERE result::text LIKE %s",
            (f"%for {order}%",),
        )
        rows = await cur.fetchall()
    assert rows == [("done", f"refund #1 for {order}")]
    assert MEMORY_LEDGER._entries == before, "the entry went to process memory, not Postgres"
