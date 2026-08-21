"""Runtime span-tree integration: a real engine run emits the full nested trace (P07 DoD).

The unit tests in ``test_tracing_callback.py`` drive the callback methods by hand; this proves the
wiring end to end. It runs an actual ``langgraph`` agent — root ``agent`` span opened by the
runtime, a fake tool-calling model in place of LiteLLM, the built-in ``calculator`` bound as tool —
against a vendor-free :class:`RecordingObserver`, then asserts the captured tree has what every P07
backend must show: a ``model`` span carrying tokens and cost, a ``tool.calculator`` span, and a
``node.*`` span, all nested under the one root. This is the "not just a bare AGENT span" bar the
rebuild exists to clear, checked without standing up a collector.
"""

from __future__ import annotations

import agentship_langgraph.models as models_module
import pytest
from agentship.observability import RecordingObserver, semconv
from agentship.runtime import build_agent
from agentship.spec import AgentSpec
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult


class _ToolThenAnswerModel(BaseChatModel):
    """A deterministic fake: the first call requests the calculator, the second answers.

    This is the minimal shape of a ReAct turn — an assistant tool-call message, then a final
    answer — so a real ``create_react_agent`` loop drives two model calls with a tool call between.
    Both messages carry ``usage_metadata`` so the callback has real token counts to stamp.
    """

    calls: int = 0
    #: The LiteLLM model id this fake reports, so the callback can price the call via LiteLLM.
    model: str = "openai/gpt-4o-mini"

    @property
    def _llm_type(self) -> str:
        """LangChain model-type tag (unused here but required by the base class)."""
        return "fake-tool-then-answer"

    @property
    def _identifying_params(self) -> dict[str, str]:
        """Surface the model id in the callback's ``invocation_params`` (as a real model would)."""
        return {"model": self.model}

    def bind_tools(self, tools: object, **kwargs: object) -> BaseChatModel:
        """Accept the ReAct loop's tool binding and stay the same fake model."""
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        """Return a tool-call message on the first turn and a final answer on the second."""
        self.calls += 1
        if self.calls == 1:
            message = AIMessage(
                content="",
                tool_calls=[
                    {"name": "calculator", "args": {"expression": "2 + 2"}, "id": "call_1"}
                ],
                usage_metadata={"input_tokens": 5, "output_tokens": 2, "total_tokens": 7},
            )
        else:
            message = AIMessage(
                content="The answer is 4.",
                usage_metadata={"input_tokens": 8, "output_tokens": 3, "total_tokens": 11},
            )
        return ChatResult(generations=[ChatGeneration(message=message)])


@pytest.fixture
def tool_calling_model(monkeypatch):
    """Inject the tool-calling fake in place of the real LiteLLM model (no network)."""
    model = _ToolThenAnswerModel()
    monkeypatch.setattr(models_module, "resolve_model", lambda *a, **k: model)
    return model


async def test_real_run_emits_model_tool_and_node_spans_under_the_root(tool_calling_model):
    """A live engine run records agent → node → {model, tool.calculator} with tokens and cost."""
    observer = RecordingObserver()
    agent = build_agent(
        AgentSpec(
            name="a",
            engine="langgraph",
            template="single",  # the prebuilt ReAct loop that actually executes tools
            model="openai/gpt-4o-mini",  # a real id so LiteLLM prices the call
            prompt="You do math.",
            tools=["calculator"],
        ),
        observer=observer,
    )

    result = await agent.run("What is 2 + 2?")
    assert "4" in result.output

    view = observer.trace_view()
    assert view.root.name == semconv.SPAN_AGENT

    model_spans = list(view.model_spans())
    assert len(model_spans) == 2, "both ReAct model calls should be traced"
    first = model_spans[0]
    assert first.attrs[semconv.GEN_AI_USAGE_INPUT_TOKENS] == 5
    assert first.attrs[semconv.GEN_AI_USAGE_OUTPUT_TOKENS] == 2
    assert first.attrs[semconv.AS_COST_USD] > 0, "LiteLLM should price a known model"
    assert first.attrs[semconv.GEN_AI_REQUEST_MODEL] == "openai/gpt-4o-mini"

    tool_calls = list(view.tool_calls())
    assert [t.name for t in tool_calls] == [semconv.tool_span("calculator")]

    node_names = [s.name for s in view.spans() if s.name.startswith(semconv.NODE_PREFIX)]
    assert node_names, "the LangGraph node boundary should be traced as a node.* span"


class _StreamingAnswerModel(BaseChatModel):
    """A fake that answers in one turn with usage — the shape of a streamed model call."""

    model: str = "openai/gpt-4o-mini"

    @property
    def _llm_type(self) -> str:
        """LangChain model-type tag."""
        return "fake-streaming-answer"

    @property
    def _identifying_params(self) -> dict[str, str]:
        """Surface the model id so the callback can price the streamed call."""
        return {"model": self.model}

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        """Return a single answer carrying token usage, as a real final stream chunk would."""
        message = AIMessage(
            content="Hello there",
            usage_metadata={"input_tokens": 4, "output_tokens": 2, "total_tokens": 6},
        )
        return ChatResult(generations=[ChatGeneration(message=message)])


async def test_streamed_run_records_tokens_and_cost_on_the_model_span(monkeypatch):
    """A streamed turn stamps the same tokens/cost a plain run would — usage is not lost.

    LangGraph streams with ``stream_mode="messages"``; the callback still gets a single
    ``on_llm_end`` carrying the aggregated usage, so streaming and invoking record identical model
    attributes. This guards the streamed path against silently dropping token/cost capture.
    """
    monkeypatch.setattr(models_module, "resolve_model", lambda *a, **k: _StreamingAnswerModel())
    observer = RecordingObserver()
    agent = build_agent(
        AgentSpec(
            name="a", engine="langgraph", template="single", model="openai/gpt-4o-mini", prompt="p"
        ),
        observer=observer,
    )

    events = [event async for event in agent.stream("hi")]
    assert any(event.type == "content" for event in events)

    model = next(iter(observer.trace_view().model_spans()))
    assert model.attrs[semconv.GEN_AI_USAGE_INPUT_TOKENS] == 4
    assert model.attrs[semconv.GEN_AI_USAGE_OUTPUT_TOKENS] == 2
    assert model.attrs[semconv.AS_COST_USD] > 0


async def test_content_is_not_captured_by_default(tool_calling_model):
    """With the PHI gate off (default), no model span carries prompt/response text."""
    observer = RecordingObserver()
    agent = build_agent(
        AgentSpec(name="a", engine="langgraph", model="x", prompt="p", tools=["calculator"]),
        observer=observer,
    )

    await agent.run("What is 2 + 2?")

    for model in observer.trace_view().model_spans():
        assert semconv.GEN_AI_INPUT_MESSAGES not in model.attrs
        assert semconv.GEN_AI_OUTPUT_MESSAGES not in model.attrs
