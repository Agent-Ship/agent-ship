"""Parity: the production callback builds the same tree on the vendor-free and OTel observers.

``test_observer_parity.py`` (in ``agentship-observability``) proves the raw ``start_span`` primitive
agrees across observers; this proves the layer that *uses* it — :class:`ObservabilityCallback`, the
real P07 path — does too. It cannot live there because that package must not depend on
``agentship-langgraph`` (the dependency runs the other way), so the callback-level guard lives here,
where both the callback and the OTel observer are importable.

The same synthetic LangChain event stream (a node with a model call and a tool call) is driven
through the callback against a :class:`RecordingObserver` and an :class:`OTelObserver`, and the two
captured trees must agree on structure (span-name nesting) and on the model span's semconv
attributes. So the vendor-free double stays a faithful stand-in for what the callback actually
records in production — any drift fails CI instead of misleading an offline verifier.
"""

from __future__ import annotations

from collections.abc import Iterator
from uuid import uuid4

import pytest
from agentship.observability import RecordingObserver, SpanKind, semconv
from agentship.observability.trace_view import SpanNode
from agentship_langgraph.tracing import ObservabilityCallback
from agentship_observability import OTelObserver
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, LLMResult
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

pytestmark = pytest.mark.asyncio

# The model-span attributes an offline verifier (P12) reads; both observers must carry them.
_MODEL_ATTR_KEYS = (
    semconv.GEN_AI_SYSTEM,
    semconv.GEN_AI_REQUEST_MODEL,
    semconv.GEN_AI_USAGE_INPUT_TOKENS,
    semconv.GEN_AI_USAGE_OUTPUT_TOKENS,
    semconv.AS_COST_USD,
)


def _llm_result() -> LLMResult:
    """A chat result with usage and a finish reason, as a real model reports on completion."""
    message = AIMessage(
        content="4",
        usage_metadata={"input_tokens": 5, "output_tokens": 2, "total_tokens": 7},
    )
    generation = ChatGeneration(message=message, generation_info={"finish_reason": "stop"})
    return LLMResult(generations=[[generation]], llm_output={"model_name": "gpt-4o-mini"})


async def _drive(observer) -> None:
    """Run one node → {model, tool} turn through the callback under an open root ``agent`` span.

    The root span is opened with the observer's own ``span`` context manager so it is the ambient
    span while the callback runs — exactly as the runtime opens it around an engine turn — so the
    callback's top-level spans nest under it on both observers.
    """
    callback = ObservabilityCallback(observer)
    node_id, model_id, tool_id = uuid4(), uuid4(), uuid4()
    with observer.span(semconv.SPAN_AGENT, SpanKind.AGENT):
        await callback.on_chain_start(
            {"name": "agent"}, {}, run_id=node_id, metadata={"langgraph_node": "agent"}
        )
        await callback.on_chat_model_start(
            {"kwargs": {"model": "openai/gpt-4o-mini"}},
            [[HumanMessage(content="2 + 2?")]],
            run_id=model_id,
            parent_run_id=node_id,
            invocation_params={"model": "openai/gpt-4o-mini"},
        )
        await callback.on_llm_end(_llm_result(), run_id=model_id)
        await callback.on_tool_start(
            {"name": "calculator"}, "{}", run_id=tool_id, parent_run_id=node_id
        )
        await callback.on_tool_end("4", run_id=tool_id)
        await callback.on_chain_end({}, run_id=node_id)


def _recording_edges(root: SpanNode) -> list[tuple[str, str]]:
    """Flatten a captured tree into sorted (parent_name, name) edges (root parent = '')."""

    def walk(node: SpanNode, parent: str) -> Iterator[tuple[str, str]]:
        yield (parent, node.name)
        for child in node.children:
            yield from walk(child, node.name)

    return sorted(walk(root, ""))


def _otel_edges(exporter: InMemorySpanExporter) -> list[tuple[str, str]]:
    """Flatten exported OTel spans into the same (parent_name, name) edge form."""
    spans = exporter.get_finished_spans()
    name_by_id = {span.context.span_id: span.name for span in spans}
    return sorted(
        (name_by_id.get(span.parent.span_id) if span.parent else "", span.name) for span in spans
    )


def _as_list(value: object) -> list:
    """Normalise a scalar or sequence attribute to a list for cross-observer comparison."""
    return list(value) if isinstance(value, (list, tuple)) else [value]


async def test_callback_tree_structure_matches_across_observers() -> None:
    """The callback nests agent → node → {model, tool} identically on both observers."""
    recording = RecordingObserver()
    await _drive(recording)

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    await _drive(OTelObserver(provider))

    assert _recording_edges(recording.trace_view().root) == _otel_edges(exporter)


async def test_callback_model_attributes_match_across_observers() -> None:
    """The callback stamps the same model-span semconv keys/values on both observers."""
    recording = RecordingObserver()
    await _drive(recording)
    rec_model = next(iter(recording.trace_view().model_spans()))

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    await _drive(OTelObserver(provider))
    otel_model = next(
        s for s in exporter.get_finished_spans() if s.name.startswith(semconv.MODEL_SPAN_PREFIX)
    )

    for key in _MODEL_ATTR_KEYS:
        assert key in rec_model.attrs, f"RecordingObserver dropped {key}"
        assert key in otel_model.attributes, f"OTelObserver dropped {key}"
        assert list(_as_list(rec_model.attrs[key])) == list(_as_list(otel_model.attributes[key]))
