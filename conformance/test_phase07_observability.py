"""Phase 07 conformance cells: the frozen observability contract (§6, CONF-OBS-1..6).

Named cells that pin the promises P12 (eval) and P13 (audit) build on. They assert the contract
itself — the span tree shape, the model-span GenAI/cost keys, the frozen span names, the PHI content
gate, observer fail-open, and the ``TraceView`` + replay-hash read-ports — against the vendor-free
``RecordingObserver`` plus the real ``OTelObserver`` for the fail-open path. ``SEMCONV.md`` is the
golden reference these cells enforce.
"""

from __future__ import annotations

from agentship.observability import (
    RecordingObserver,
    SpanKind,
    SpanNode,
    Usage,
    hashed_user_id,
    replay_attributes,
    request_hash,
    semconv,
)
from agentship.runtime import build_agent
from agentship.spec import AgentSpec
from agentship_observability import OTelObserver
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult


def _model_usage() -> Usage:
    """A representative model-call usage roll-up for the model-span cell."""
    return Usage(
        model="openai/gpt-4o-mini",
        provider="openai",
        input_tokens=10,
        output_tokens=5,
        cost_usd=0.0002,
        latency_ms=120.0,
        finish_reasons=["stop"],
    )


async def test_conf_obs_1_one_root_agent_span_per_run() -> None:
    """CONF-OBS-1: every agent run emits exactly one root ``agent`` span."""
    obs = RecordingObserver()
    agent = build_agent(AgentSpec(name="support", engine="echo"), observer=obs)
    await agent.run("hi", user_id="u1")
    assert len(obs.roots) == 1
    assert obs.roots[0].name.startswith(semconv.SPAN_AGENT)
    assert obs.roots[0].kind is SpanKind.AGENT


def test_conf_obs_2_model_span_carries_cost_and_token_keys() -> None:
    """CONF-OBS-2: a ``model`` span carries request model, token counts, cost, and latency."""
    obs = RecordingObserver()
    with obs.span(semconv.SPAN_AGENT, SpanKind.AGENT):
        with obs.span(semconv.SPAN_MODEL, SpanKind.LLM, {semconv.GEN_AI_REQUEST_MODEL: "m"}):
            obs.on_model(_model_usage())
    model = list(obs.trace_view().model_spans())[0]
    for key in (
        semconv.GEN_AI_REQUEST_MODEL,
        semconv.GEN_AI_USAGE_INPUT_TOKENS,
        semconv.GEN_AI_USAGE_OUTPUT_TOKENS,
        semconv.AS_COST_USD,
        semconv.AS_LATENCY_MS,
    ):
        assert key in model.attrs


def test_conf_obs_3_span_names_are_frozen() -> None:
    """CONF-OBS-3: the §4.1 span names match the golden list in SEMCONV.md (byte-stable)."""
    assert semconv.SPAN_AGENT == "agent"
    assert semconv.SPAN_MODEL == "model"
    assert semconv.SPAN_GUARDRAIL_INPUT == "guardrail.input"
    assert semconv.SPAN_GUARDRAIL_OUTPUT == "guardrail.output"
    assert semconv.SPAN_MEMORY_RECALL == "memory.recall"
    assert semconv.SPAN_MEMORY_WRITE == "memory.write"
    assert semconv.SPAN_OUTPUT_VALIDATE == "output.validate"
    assert semconv.node_span("classify") == "node.classify"
    assert semconv.tool_span("search") == "tool.search"


async def test_conf_obs_4_capture_off_leaks_no_content() -> None:
    """CONF-OBS-4: capture_content=false keeps message/tool/memory content off every span."""
    # The record/replay path emits only the hash, never the request/response bodies.
    attrs = replay_attributes(
        {"model": "m", "messages": [{"role": "user", "content": "secret"}]},
        None,
        capture_content=False,
    )
    assert semconv.GEN_AI_INPUT_MESSAGES not in attrs
    assert semconv.GEN_AI_OUTPUT_MESSAGES not in attrs

    # A real run stamps only identity keys on the root — no input text, and the user id is hashed.
    obs = RecordingObserver()
    agent = build_agent(AgentSpec(name="a", engine="echo"), observer=obs)
    await agent.run("my secret prompt", user_id="alice")
    root = obs.roots[0]
    assert "my secret prompt" not in repr(root.attrs)
    assert root.attrs[semconv.AS_USER_ID] == hashed_user_id("alice")
    assert "alice" not in root.attrs[semconv.AS_USER_ID]


async def test_conf_obs_5_observer_is_fail_open_under_a_raising_exporter() -> None:
    """CONF-OBS-5: an exporter that raises on export does not fail the agent run."""

    class _RaisingExporter(SpanExporter):
        """A span exporter that always raises — stands in for a broken trace backend."""

        def export(self, spans) -> SpanExportResult:
            """Raise as if the backend were unreachable."""
            raise RuntimeError("exporter down")

        def shutdown(self) -> None:
            """Nothing to release."""

    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(_RaisingExporter()))
    agent = build_agent(AgentSpec(name="a", engine="echo"), observer=OTelObserver(provider))
    result = await agent.run("hi", user_id="u1")
    assert result.output == "echo: hi"


def test_conf_obs_6_trace_view_shape_and_replay_hash_are_frozen() -> None:
    """CONF-OBS-6: the ``SpanNode`` shape/accessors and replay hash are the frozen P12 contract."""
    fields = SpanNode.__dataclass_fields__
    assert set(fields) == {"name", "kind", "attrs", "status", "children"}

    obs = RecordingObserver()
    with obs.span(semconv.SPAN_AGENT, SpanKind.AGENT):
        with obs.span(semconv.tool_span("search"), SpanKind.TOOL):
            pass
    view = obs.trace_view()
    assert hasattr(view, "spans") and hasattr(view, "model_spans") and hasattr(view, "tool_calls")
    assert list(view.tool_calls())[0].name == "tool.search"

    same = {"model": "m", "messages": [{"role": "user", "content": "x"}]}
    changed = {"model": "m", "messages": [{"role": "user", "content": "y"}]}
    assert request_hash(same) == request_hash(same)
    assert request_hash(same) != request_hash(changed)
