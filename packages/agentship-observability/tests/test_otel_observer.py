"""``OTelObserver`` builds the span tree and stamps GenAI attributes (P07 · C1).

These run entirely against an ``InMemorySpanExporter`` — no collector, no backend — so they are a
CI-gate check of the span factory itself: nesting, error status on a raise, the OTel kind mapping,
and the cost/token/latency roll-up ``on_model`` writes onto a ``model`` span.
"""

from __future__ import annotations

import pytest
from agentship.observability import SpanKind, Usage, semconv
from agentship_observability import OTelObserver
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind as OTelSpanKind
from opentelemetry.trace import StatusCode


@pytest.fixture
def recorded() -> tuple[OTelObserver, InMemorySpanExporter]:
    """An ``OTelObserver`` wired to an in-memory exporter, plus the exporter to read spans back."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return OTelObserver(provider), exporter


def _by_name(exporter: InMemorySpanExporter) -> dict[str, object]:
    """Index the exported finished spans by name for easy assertions."""
    return {span.name: span for span in exporter.get_finished_spans()}


def test_span_opens_closes_and_sets_attributes(recorded) -> None:
    """A span is exported with its opening attributes and default (unset → ok) status."""
    obs, exporter = recorded
    with obs.span("agent", SpanKind.AGENT, {semconv.AS_AGENT_NAME: "support"}) as span:
        span.set_attribute(semconv.AS_STATUS, "ok")
    spans = _by_name(exporter)
    assert "agent" in spans
    assert spans["agent"].attributes[semconv.AS_AGENT_NAME] == "support"
    assert spans["agent"].attributes[semconv.AS_STATUS] == "ok"
    assert spans["agent"].status.status_code is not StatusCode.ERROR


def test_child_span_nests_under_parent(recorded) -> None:
    """A span opened inside another shares the trace and parents under it (OTel context)."""
    obs, exporter = recorded
    with obs.span("agent", SpanKind.AGENT):
        with obs.span("model", SpanKind.LLM):
            pass
    spans = _by_name(exporter)
    agent, model = spans["agent"], spans["model"]
    assert model.parent is not None
    assert model.parent.span_id == agent.context.span_id
    assert model.context.trace_id == agent.context.trace_id


def test_llm_kind_maps_to_otel_client(recorded) -> None:
    """A model span carries OTel ``CLIENT`` kind; an internal one carries ``INTERNAL``."""
    obs, exporter = recorded
    with obs.span("agent", SpanKind.AGENT):
        with obs.span("model", SpanKind.LLM):
            pass
    spans = _by_name(exporter)
    assert spans["model"].kind is OTelSpanKind.CLIENT
    assert spans["agent"].kind is OTelSpanKind.INTERNAL


def test_exception_in_block_sets_error_status_and_reraises(recorded) -> None:
    """A raise inside the block records the exception, sets ERROR status, and propagates."""
    obs, exporter = recorded
    with pytest.raises(ValueError):
        with obs.span("agent", SpanKind.AGENT):
            raise ValueError("boom")
    span = _by_name(exporter)["agent"]
    assert span.status.status_code is StatusCode.ERROR
    assert any(e.name == "exception" for e in span.events)


def test_on_model_stamps_usage_onto_the_model_span(recorded) -> None:
    """``on_model`` writes GenAI + OpenInference token/cost/latency keys onto the model span."""
    obs, exporter = recorded
    with obs.span("agent", SpanKind.AGENT):
        with obs.span("model", SpanKind.LLM, {semconv.GEN_AI_REQUEST_MODEL: "openai/gpt-4o-mini"}):
            obs.on_model(
                Usage(
                    model="openai/gpt-4o-mini",
                    provider="openai",
                    input_tokens=10,
                    output_tokens=5,
                    cost_usd=0.0002,
                    latency_ms=333.0,
                    finish_reasons=["stop"],
                    response_model="gpt-4o-mini-2024-07-18",
                )
            )
    model = _by_name(exporter)["model"]
    assert model.attributes[semconv.GEN_AI_USAGE_INPUT_TOKENS] == 10
    assert model.attributes[semconv.OI_TOKEN_COUNT_TOTAL] == 15
    assert model.attributes[semconv.AS_COST_USD] == pytest.approx(0.0002)
    assert model.attributes[semconv.GEN_AI_SYSTEM] == "openai"
    assert model.attributes[semconv.GEN_AI_RESPONSE_MODEL] == "gpt-4o-mini-2024-07-18"
    assert list(model.attributes[semconv.GEN_AI_RESPONSE_FINISH_REASONS]) == ["stop"]


def test_on_model_off_a_model_span_is_a_noop(recorded) -> None:
    """Called under a non-model span, ``on_model`` stamps nothing (and never raises)."""
    obs, exporter = recorded
    with obs.span("agent", SpanKind.AGENT):
        obs.on_model(
            Usage(model="m", provider="p", input_tokens=1, output_tokens=1, cost_usd=None,
                  latency_ms=1.0)
        )
    agent = _by_name(exporter)["agent"]
    assert semconv.GEN_AI_USAGE_INPUT_TOKENS not in (agent.attributes or {})


def test_current_trace_id_matches_the_open_span(recorded) -> None:
    """``current_trace_id`` returns the active span's 32-hex trace id; ``None`` when none open."""
    obs, exporter = recorded
    assert obs.current_trace_id() is None
    with obs.span("agent", SpanKind.AGENT):
        trace_id = obs.current_trace_id()
        assert trace_id is not None and len(trace_id) == 32
    span = _by_name(exporter)["agent"]
    assert format(span.context.trace_id, "032x") == trace_id


def test_none_attributes_are_dropped_not_rejected(recorded) -> None:
    """A ``None`` attribute value is omitted (OTel rejects None) rather than crashing the span."""
    obs, exporter = recorded
    with obs.span("agent", SpanKind.AGENT, {"present": "x", "absent": None}) as span:
        span.set_attribute("also_absent", None)
    attrs = _by_name(exporter)["agent"].attributes or {}
    assert attrs["present"] == "x"
    assert "absent" not in attrs
    assert "also_absent" not in attrs
