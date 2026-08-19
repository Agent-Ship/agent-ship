"""Parity guard: the vendor-free RecordingObserver builds the same tree as the OTel pipeline.

``RecordingObserver`` lives in ``agentship-core`` and captures spans in memory with no OpenTelemetry
dependency — the kernel is vendor-free by contract, and offline verifiers (P12) plus the base
install must record traces without a collector. It is therefore *not* OTel's InMemorySpanExporter
(core cannot import OTel) and it emits our own ``SpanNode``/``TraceView`` shape.

The risk in keeping a second capture path is drift: the in-memory double could diverge from what
the production OTel observer actually records. This guard runs the same interaction through both and
asserts they agree on tree structure (span names + parent→child nesting) and on the model span's
key semantic-convention attributes. So the vendor-free double stays a faithful stand-in for the
real pipeline, and any divergence fails CI instead of misleading an offline verifier.
"""

from __future__ import annotations

from collections.abc import Iterator

from agentship.observability import RecordingObserver, SpanKind, Usage, semconv
from agentship.observability.trace_view import SpanNode
from agentship_observability import OTelObserver
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

_USAGE = Usage(
    model="openai/gpt-4o-mini",
    provider="openai",
    input_tokens=3,
    output_tokens=4,
    cost_usd=0.0001,
    latency_ms=5,
    finish_reasons=["stop"],
    response_model="gpt-4o-mini",
)

# The attributes an offline verifier (P12) reads off a model span; both observers must set them.
_MODEL_ATTR_KEYS = (
    semconv.GEN_AI_SYSTEM,
    semconv.GEN_AI_USAGE_INPUT_TOKENS,
    semconv.GEN_AI_USAGE_OUTPUT_TOKENS,
    semconv.GEN_AI_RESPONSE_FINISH_REASONS,
    semconv.AS_LATENCY_MS,
    semconv.AS_COST_USD,
)


def _run(observer) -> None:
    """Drive one representative interaction: agent → node → model with usage stamped."""
    with observer.span("agent", SpanKind.AGENT):
        with observer.span("node.chat", SpanKind.INTERNAL):
            with observer.span("model", SpanKind.LLM):
                observer.on_model(_USAGE)


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


def test_span_tree_structure_matches() -> None:
    """Both observers produce the identical span-name nesting for the same interaction."""
    recording = RecordingObserver()
    _run(recording)

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    _run(OTelObserver(provider))

    assert _recording_edges(recording.trace_view().root) == _otel_edges(exporter)


def test_model_span_attributes_match() -> None:
    """Both observers stamp the same semantic-convention keys/values on the model span."""
    recording = RecordingObserver()
    _run(recording)
    rec_model = next(iter(recording.trace_view().model_spans()))

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    _run(OTelObserver(provider))
    otel_model = next(s for s in exporter.get_finished_spans() if s.name == "model")

    for key in _MODEL_ATTR_KEYS:
        assert key in rec_model.attrs, f"RecordingObserver dropped {key}"
        assert key in otel_model.attributes, f"OTelObserver dropped {key}"
        # finish_reasons is a list on our node but an immutable tuple on the OTel span.
        assert list(_as_list(rec_model.attrs[key])) == list(_as_list(otel_model.attributes[key]))


def _as_list(value: object) -> list:
    """Normalise a scalar or sequence attribute to a list for cross-observer comparison."""
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]
