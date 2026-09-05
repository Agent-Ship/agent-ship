"""The vendor-free observability contract: the port, the recorder, and the TraceView (P07 · C1).

These assert the kernel half of tracing behaves before any OTel wiring exists: the
:class:`NoOpObserver` honours the ``span`` contract with tracing off, the :class:`RecordingObserver`
builds the canonical tree and stamps model usage, and :class:`TraceView` reads it by the frozen
accessors P12 depends on.
"""

from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError

import pytest
from agentship.observability import (
    NoOpObserver,
    RecordingObserver,
    SpanKind,
    TraceView,
    Usage,
    semconv,
    usage_attributes,
)

_USAGE = Usage(
    model="openai/gpt-4o-mini",
    provider="openai",
    input_tokens=12,
    output_tokens=7,
    cost_usd=0.0003,
    latency_ms=421.0,
    finish_reasons=["stop"],
    response_model="gpt-4o-mini-2024-07-18",
)


def test_noop_observer_yields_a_span_and_reraises() -> None:
    """The no-op observer honours the context-manager contract: it yields, and never swallows."""
    obs = NoOpObserver()
    with obs.span("agent", SpanKind.AGENT) as span:
        span.set_attribute("k", "v")  # accepted and dropped
    assert obs.current_trace_id() is None
    with pytest.raises(ValueError):
        with obs.span("agent", SpanKind.AGENT):
            raise ValueError("boom")


def test_recording_observer_builds_a_nested_tree() -> None:
    """Spans opened inside a parent nest under it; the recorder mints one trace id."""
    obs = RecordingObserver()
    with obs.span("agent", SpanKind.AGENT, {semconv.AS_AGENT_NAME: "support"}):
        with obs.span(semconv.node_span("classify"), SpanKind.INTERNAL):
            with obs.span("model", SpanKind.LLM):
                pass
    view = obs.trace_view()
    assert view.root.name == "agent"
    assert view.root.attrs[semconv.AS_AGENT_NAME] == "support"
    classify = view.root.children[0]
    assert classify.name == "node.classify"
    assert classify.children[0].name == "model"
    assert obs.current_trace_id() is not None


def test_recording_observer_marks_error_status_and_reraises() -> None:
    """A raise inside the block errors the span and still propagates the exception."""
    obs = RecordingObserver()
    with pytest.raises(RuntimeError):
        with obs.span("agent", SpanKind.AGENT):
            raise RuntimeError("kaboom")
    root = obs.trace_view().root
    assert root.status == "error"
    assert root.attrs["exception.type"] == "RuntimeError"


def test_on_model_stamps_usage_onto_the_model_span() -> None:
    """``on_model`` writes GenAI + OpenInference token/cost/latency keys onto the model span."""
    obs = RecordingObserver()
    with obs.span("agent", SpanKind.AGENT):
        with obs.span("model", SpanKind.LLM):
            obs.on_model(
                Usage(
                    model="openai/gpt-4o-mini",
                    provider="openai",
                    input_tokens=12,
                    output_tokens=7,
                    cost_usd=0.0003,
                    latency_ms=421.0,
                    finish_reasons=["stop"],
                    response_model="gpt-4o-mini-2024-07-18",
                )
            )
    model = next(obs.trace_view().model_spans())
    assert model.attrs[semconv.GEN_AI_USAGE_INPUT_TOKENS] == 12
    assert model.attrs[semconv.GEN_AI_USAGE_OUTPUT_TOKENS] == 7
    assert model.attrs[semconv.OI_TOKEN_COUNT_TOTAL] == 19
    assert model.attrs[semconv.AS_COST_USD] == 0.0003
    assert model.attrs[semconv.GEN_AI_SYSTEM] == "openai"
    assert model.attrs[semconv.GEN_AI_RESPONSE_MODEL] == "gpt-4o-mini-2024-07-18"


def test_on_model_is_a_noop_off_a_model_span() -> None:
    """Called when the active span is not a model span, ``on_model`` drops the usage (no crash)."""
    obs = RecordingObserver()
    with obs.span("agent", SpanKind.AGENT):
        obs.on_model(
            Usage(
                model="m",
                provider="p",
                input_tokens=1,
                output_tokens=1,
                cost_usd=None,
                latency_ms=1.0,
            )
        )
    root = obs.trace_view().root
    assert semconv.GEN_AI_USAGE_INPUT_TOKENS not in root.attrs


def test_trace_view_accessors_find_models_and_tools() -> None:
    """``model_spans``/``tool_calls`` reach every LLM/tool span wherever it sits in the tree."""
    obs = RecordingObserver()
    with obs.span("agent", SpanKind.AGENT):
        with obs.span(semconv.node_span("act"), SpanKind.INTERNAL):
            with obs.span("model", SpanKind.LLM):
                pass
            with obs.span(semconv.tool_span("search"), SpanKind.TOOL, {"q": "x"}):
                pass
    view = obs.trace_view()
    assert [s.name for s in view.model_spans()] == ["model"]
    tools = list(view.tool_calls())
    assert [t.name for t in tools] == ["tool.search"]
    assert tools[0].attrs["q"] == "x"
    # spans(name) filters; spans() yields the whole tree.
    assert len(list(view.spans())) == 4
    assert len(list(view.spans("model"))) == 1


def test_recorder_nests_correctly_under_concurrent_tasks() -> None:
    """Two concurrent tasks each nest their model span under their own node — no cross-parenting."""
    obs = RecordingObserver()

    async def one_node(node_name: str) -> None:
        with obs.span(semconv.node_span(node_name), SpanKind.INTERNAL):
            await asyncio.sleep(0)
            with obs.span("model", SpanKind.LLM):
                await asyncio.sleep(0)

    async def drive() -> None:
        with obs.span("agent", SpanKind.AGENT):
            await asyncio.gather(one_node("a"), one_node("b"))

    asyncio.run(drive())
    root = obs.trace_view().root
    assert {c.name for c in root.children} == {"node.a", "node.b"}
    for child in root.children:
        assert [g.name for g in child.children] == ["model"]


def test_span_node_is_frozen() -> None:
    """A captured :class:`SpanNode` is immutable — a verifier can hold it safely across a run."""
    obs = RecordingObserver()
    with obs.span("agent", SpanKind.AGENT):
        pass
    node = obs.trace_view().root
    with pytest.raises(FrozenInstanceError):
        node.name = "mutated"  # type: ignore[misc]


def test_trace_view_is_constructible_from_a_bare_node() -> None:
    """``TraceView`` reads any ``SpanNode`` tree, not just the recorder's (P12 relies on this)."""
    obs = RecordingObserver()
    with obs.span("agent", SpanKind.AGENT):
        with obs.span("model", SpanKind.LLM):
            pass
    view = TraceView(obs.roots[0])
    assert view.root.name == "agent"
    assert len(list(view.model_spans())) == 1


def test_usage_attributes_maps_every_model_span_key() -> None:
    """The shared helper emits the GenAI + OpenInference + AgentShip keys a model span carries."""
    attrs = usage_attributes(_USAGE)
    assert attrs[semconv.GEN_AI_SYSTEM] == "openai"
    assert attrs[semconv.GEN_AI_USAGE_INPUT_TOKENS] == 12
    assert attrs[semconv.GEN_AI_USAGE_OUTPUT_TOKENS] == 7
    assert attrs[semconv.OI_TOKEN_COUNT_TOTAL] == 19
    assert attrs[semconv.GEN_AI_RESPONSE_FINISH_REASONS] == ["stop"]
    assert attrs[semconv.AS_LATENCY_MS] == 421.0
    assert attrs[semconv.AS_COST_USD] == 0.0003
    assert attrs[semconv.GEN_AI_RESPONSE_MODEL] == "gpt-4o-mini-2024-07-18"


def test_usage_attributes_omits_absent_cost_and_response_model() -> None:
    """A local model reports no price/echoed id — those keys stay off rather than write ``None``."""
    attrs = usage_attributes(
        Usage(
            model="ollama/llama3",
            provider="ollama",
            input_tokens=2,
            output_tokens=3,
            cost_usd=None,
            latency_ms=9.0,
            finish_reasons=[],
        )
    )
    assert semconv.AS_COST_USD not in attrs
    assert semconv.GEN_AI_RESPONSE_MODEL not in attrs
    assert attrs[semconv.OI_TOKEN_COUNT_TOTAL] == 5


def test_start_span_nests_under_explicit_parent_across_call_boundaries() -> None:
    """``start_span`` rebuilds a tree from explicit parents — the callback-driven nesting path."""
    obs = RecordingObserver()
    root = obs.start_span("agent", SpanKind.AGENT)
    node = obs.start_span("node.agent", SpanKind.INTERNAL, parent=root)
    model = obs.start_span("model", SpanKind.LLM, parent=node)
    model.set_attributes(usage_attributes(_USAGE))
    model.end()
    node.end()
    root.end()

    view = TraceView(obs.roots[0])
    assert view.root.name == "agent"
    assert [c.name for c in view.root.children] == ["node.agent"]
    assert [c.name for c in view.root.children[0].children] == ["model"]
    model_node = next(iter(view.model_spans()))
    assert model_node.attrs[semconv.GEN_AI_USAGE_INPUT_TOKENS] == 12
    assert model_node.attrs[semconv.AS_COST_USD] == 0.0003


def test_start_span_without_parent_nests_under_the_current_span() -> None:
    """With no explicit parent, ``start_span`` lands under the task's open span (the root agent)."""
    obs = RecordingObserver()
    with obs.span("agent", SpanKind.AGENT):
        child = obs.start_span("node.agent", SpanKind.INTERNAL)
        child.end()
    assert [c.name for c in obs.roots[0].children] == ["node.agent"]


def test_noop_start_span_returns_a_usable_span() -> None:
    """The no-op observer's ``start_span`` yields a span whose methods are safe to call."""
    span = NoOpObserver().start_span("model", SpanKind.LLM)
    span.set_attribute("k", "v")
    span.set_error(RuntimeError("x"))
    span.end()  # must not raise
