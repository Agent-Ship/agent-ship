"""The LiteLLM callback stamps replay attributes onto the model span (P07 · §4.10).

Proves the wiring end to end through the callback: the request hash always lands on the model span,
and the request/response payload lands only when the observer captures content. Uses the
backend-agnostic ``RecordingObserver`` and the OTel observer to cover both the eval read-port and
the production path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from agentship.observability import RecordingObserver, SpanKind, semconv
from agentship_observability import OTelObserver
from agentship_observability.litellm_logger import AgentShipLiteLLMLogger
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


@dataclass
class _Usage:
    prompt_tokens: int = 3
    completion_tokens: int = 4
    total_tokens: int = 7


@dataclass
class _Message:
    role: str = "assistant"
    content: str = "the answer"


@dataclass
class _Choice:
    finish_reason: str = "stop"
    message: _Message = field(default_factory=_Message)


@dataclass
class _Response:
    model: str = "gpt-4o-mini"
    usage: _Usage = field(default_factory=_Usage)
    choices: list[_Choice] = field(default_factory=lambda: [_Choice()])


def _kwargs():
    """A LiteLLM success ``kwargs`` carrying the secret prompt."""
    return {
        "model": "openai/gpt-4o-mini",
        "messages": [{"role": "user", "content": "my secret"}],
        "optional_params": {"temperature": 0.0},
        "response_cost": 0.0001,
    }


def test_request_hash_lands_and_content_is_gated_off_by_default() -> None:
    """Default (capture off): the model span gets the hash but never the prompt content."""
    obs = RecordingObserver()
    logger = AgentShipLiteLLMLogger(obs)
    with obs.span("agent", SpanKind.AGENT):
        with obs.span("model", SpanKind.LLM):
            logger.log_success_event(
                _kwargs(), _Response(), datetime(2026, 1, 1), datetime(2026, 1, 1)
            )
    model = list(obs.trace_view().model_spans())[0]
    assert model.attrs[semconv.AS_REPLAY_REQUEST_HASH]
    assert semconv.GEN_AI_INPUT_MESSAGES not in model.attrs


def test_content_captured_when_observer_allows_it() -> None:
    """An OTel observer built with capture_content=True records request + response for replay."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    obs = OTelObserver(provider, capture_content=True)
    logger = AgentShipLiteLLMLogger(obs)
    with obs.span("agent", SpanKind.AGENT):
        with obs.span("model", SpanKind.LLM):
            logger.log_success_event(
                _kwargs(), _Response(), datetime(2026, 1, 1), datetime(2026, 1, 1)
            )
    model = next(s for s in exporter.get_finished_spans() if s.name == "model")
    assert model.attributes[semconv.AS_REPLAY_REQUEST_HASH]
    assert "my secret" in model.attributes[semconv.GEN_AI_INPUT_MESSAGES]
    assert "the answer" in model.attributes[semconv.GEN_AI_OUTPUT_MESSAGES]
