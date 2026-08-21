"""Unit tests for :class:`ObservabilityCallback` — the LangChain→observer span translator (P07).

These drive the callback's event methods directly with hand-built LangChain payloads (no live model,
no graph) against a vendor-free :class:`RecordingObserver`, then assert on the captured span tree.
That isolates the translation logic — event → span name/kind/attrs and run_id/parent_run_id
nesting — from LangGraph's wiring, which the engine integration tests cover. The tree we expect
mirrors a real turn: a root ``agent`` span with a ``node.agent`` child, a ``model`` call under the
node, and a ``tool.search`` call under it, each carrying the frozen semconv attributes P12 reads.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from agentship.observability import RecordingObserver, semconv
from agentship_langgraph.tracing import ObservabilityCallback
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, LLMResult

pytestmark = pytest.mark.asyncio


def _llm_result(*, input_tokens: int = 11, output_tokens: int = 7) -> LLMResult:
    """Build a chat ``LLMResult`` with usage metadata and a finish reason, as a real model would."""
    message = AIMessage(
        content="done",
        usage_metadata={
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        },
    )
    generation = ChatGeneration(message=message, generation_info={"finish_reason": "stop"})
    return LLMResult(generations=[[generation]], llm_output={"model_name": "gpt-4o-mini"})


async def test_model_call_records_tokens_cost_and_identity() -> None:
    """A start/end model event pair yields one ``model`` span with tokens, cost and model id."""
    observer = RecordingObserver()
    callback = ObservabilityCallback(observer)
    run_id = uuid4()

    await callback.on_chat_model_start(
        {"kwargs": {"model": "openai/gpt-4o-mini"}},
        [[HumanMessage(content="hi")]],
        run_id=run_id,
        invocation_params={"model": "openai/gpt-4o-mini", "temperature": 0.2},
    )
    await callback.on_llm_end(_llm_result(), run_id=run_id)

    model = observer.roots[0]
    assert model.name == semconv.SPAN_MODEL
    assert model.attrs[semconv.GEN_AI_REQUEST_MODEL] == "openai/gpt-4o-mini"
    assert model.attrs[semconv.GEN_AI_SYSTEM] == "openai"
    assert model.attrs[semconv.GEN_AI_USAGE_INPUT_TOKENS] == 11
    assert model.attrs[semconv.GEN_AI_USAGE_OUTPUT_TOKENS] == 7
    assert model.attrs[semconv.AS_COST_USD] > 0
    assert semconv.AS_REPLAY_REQUEST_HASH in model.attrs


async def test_tree_nests_model_and_tool_under_node_under_agent() -> None:
    """run_id/parent_run_id links rebuild node → {model, tool} even across separate events."""
    observer = RecordingObserver()
    callback = ObservabilityCallback(observer)
    agent_id, node_id, model_id, tool_id = uuid4(), uuid4(), uuid4(), uuid4()

    # The runtime's root agent span; the callback nests everything under whatever it is given.
    await callback.on_chain_start({"name": "agent"}, {}, run_id=agent_id)  # not a node: no span
    await callback.on_chain_start(
        {"name": "agent"}, {}, run_id=node_id, parent_run_id=agent_id,
        metadata={"langgraph_node": "agent"},
    )
    await callback.on_chat_model_start(
        {"kwargs": {"model": "openai/gpt-4o-mini"}},
        [[HumanMessage(content="hi")]],
        run_id=model_id,
        parent_run_id=node_id,
        invocation_params={"model": "openai/gpt-4o-mini"},
    )
    await callback.on_llm_end(_llm_result(), run_id=model_id)
    await callback.on_tool_start(
        {"name": "search"}, "{}", run_id=tool_id, parent_run_id=node_id
    )
    await callback.on_tool_end("result", run_id=tool_id)
    await callback.on_chain_end({}, run_id=node_id)

    node = observer.roots[0]
    assert node.name == semconv.node_span("agent")
    child_names = sorted(child.name for child in node.children)
    assert child_names == [semconv.SPAN_MODEL, semconv.tool_span("search")]


async def test_mcp_tool_span_is_tagged_with_its_server() -> None:
    """A tool known to come from an MCP server carries ``agentship.tool.mcp_server``."""
    observer = RecordingObserver()
    callback = ObservabilityCallback(observer, mcp_servers={"fetch": "docs-server"})
    tool_id = uuid4()

    await callback.on_tool_start({"name": "fetch"}, "{}", run_id=tool_id)
    await callback.on_tool_end("ok", run_id=tool_id)

    tool = observer.roots[0]
    assert tool.name == semconv.tool_span("fetch")
    assert tool.attrs[semconv.AS_TOOL_MCP_SERVER] == "docs-server"


async def test_content_is_withheld_by_default_and_redacted_when_enabled() -> None:
    """The PHI gate governs content: off → no prompt/result attrs; on → captured but redacted."""
    withheld = RecordingObserver()
    off = ObservabilityCallback(withheld)
    run_id = uuid4()
    await off.on_tool_start(
        {"name": "email"}, "reach me at jane@example.com", run_id=run_id
    )
    await off.on_tool_end("sent to jane@example.com", run_id=run_id)
    assert semconv.GEN_AI_INPUT_MESSAGES not in withheld.roots[0].attrs
    assert semconv.GEN_AI_OUTPUT_MESSAGES not in withheld.roots[0].attrs

    captured = RecordingObserver()
    on = ObservabilityCallback(captured, capture_content=True)
    run_id = uuid4()
    await on.on_tool_start(
        {"name": "email"}, "reach me at jane@example.com", run_id=run_id
    )
    await on.on_tool_end("sent to jane@example.com", run_id=run_id)
    tool = captured.roots[0]
    assert "jane@example.com" not in tool.attrs[semconv.GEN_AI_INPUT_MESSAGES]
    assert "[REDACTED:email]" in tool.attrs[semconv.GEN_AI_INPUT_MESSAGES]
    assert "[REDACTED:email]" in tool.attrs[semconv.GEN_AI_OUTPUT_MESSAGES]


async def test_model_error_marks_the_span_errored() -> None:
    """``on_llm_error`` closes the model span with error status instead of dropping it."""
    observer = RecordingObserver()
    callback = ObservabilityCallback(observer)
    run_id = uuid4()

    await callback.on_chat_model_start(
        {"kwargs": {"model": "openai/gpt-4o-mini"}},
        [[HumanMessage(content="hi")]],
        run_id=run_id,
        invocation_params={"model": "openai/gpt-4o-mini"},
    )
    await callback.on_llm_error(RuntimeError("boom"), run_id=run_id)

    assert observer.roots[0].status == "error"
