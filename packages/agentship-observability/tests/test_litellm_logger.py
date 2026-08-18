"""The LiteLLM callback maps a success payload to model-span usage (P07 · C3).

Uses the backend-agnostic ``RecordingObserver`` from core as the sink, so the test proves the
mapping (tokens/cost/latency/finish-reasons → the model span) without OTel or a backend. Also pins
the fail-open contract and register-once idempotency.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import litellm
import pytest
from agentship.observability import RecordingObserver, SpanKind, semconv
from agentship_observability.litellm_logger import (
    AgentShipLiteLLMLogger,
    _reset_litellm_logger_for_tests,
    register_litellm_logger,
    usage_from_litellm,
)


@dataclass
class _FakeUsage:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


@dataclass
class _FakeChoice:
    finish_reason: str


@dataclass
class _FakeResponse:
    model: str
    usage: _FakeUsage
    choices: list[_FakeChoice]


def _response() -> _FakeResponse:
    """A minimal ModelResponse-shaped object like LiteLLM passes to a success callback."""
    return _FakeResponse(
        model="gpt-4o-mini-2024-07-18",
        usage=_FakeUsage(prompt_tokens=12, completion_tokens=8, total_tokens=20),
        choices=[_FakeChoice(finish_reason="stop")],
    )


@pytest.fixture(autouse=True)
def _clean_registration():
    """Ensure no logger is registered across tests."""
    _reset_litellm_logger_for_tests()
    yield
    _reset_litellm_logger_for_tests()


def test_usage_from_litellm_maps_all_fields() -> None:
    """The mapper pulls tokens, cost, latency, provider, and finish reasons off the payload."""
    start = datetime(2026, 1, 1, 0, 0, 0)
    end = start + timedelta(milliseconds=250)
    usage = usage_from_litellm(
        {"model": "openai/gpt-4o-mini", "custom_llm_provider": "openai", "response_cost": 0.0003},
        _response(),
        start,
        end,
    )
    assert usage is not None
    assert usage.provider == "openai"
    assert usage.input_tokens == 12
    assert usage.output_tokens == 8
    assert usage.cost_usd == pytest.approx(0.0003)
    assert usage.latency_ms == pytest.approx(250.0)
    assert usage.finish_reasons == ["stop"]
    assert usage.response_model == "gpt-4o-mini-2024-07-18"


def test_usage_from_litellm_without_usage_is_none() -> None:
    """No ``usage`` block on the response means nothing to stamp — return ``None``."""
    assert usage_from_litellm({}, object(), None, None) is None


def test_logger_stamps_onto_the_current_model_span() -> None:
    """Firing the success hook under a model span stamps GenAI/token attributes onto it."""
    obs = RecordingObserver()
    logger = AgentShipLiteLLMLogger(obs)
    start = datetime(2026, 1, 1)
    with obs.span("agent", SpanKind.AGENT):
        with obs.span("model", SpanKind.LLM):
            logger.log_success_event(
                {"model": "openai/gpt-4o-mini", "response_cost": 0.001},
                _response(),
                start,
                start + timedelta(milliseconds=100),
            )
    model = list(obs.trace_view().model_spans())[0]
    assert model.attrs[semconv.GEN_AI_USAGE_INPUT_TOKENS] == 12
    assert model.attrs[semconv.OI_TOKEN_COUNT_TOTAL] == 20
    assert model.attrs[semconv.AS_COST_USD] == pytest.approx(0.001)


def test_logger_is_fail_open_on_bad_payload() -> None:
    """A malformed payload is swallowed, never raised into LiteLLM's request path."""
    obs = RecordingObserver()
    logger = AgentShipLiteLLMLogger(obs)
    with obs.span("agent", SpanKind.AGENT):
        with obs.span("model", SpanKind.LLM):
            logger.log_success_event(None, None, None, None)  # must not raise


def test_register_is_idempotent() -> None:
    """Registering twice appends exactly one callback and returns the same logger."""
    before = len(litellm.callbacks)
    first = register_litellm_logger(RecordingObserver())
    second = register_litellm_logger(RecordingObserver())
    assert first is second
    assert len(litellm.callbacks) == before + 1
