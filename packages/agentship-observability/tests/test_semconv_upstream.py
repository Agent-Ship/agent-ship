"""Drift guard: our vendor-free semconv strings must equal the upstream standards.

The span-attribute keys in ``agentship.observability.semconv`` deliberately live in
``agentship-core`` as plain strings so an engine hook can import them without OpenTelemetry
installed (the kernel is vendor-free by contract). But those strings are not ours to invent
— the ``gen_ai.*`` keys are OpenTelemetry's GenAI semantic conventions and the
``llm.token_count.*`` keys are OpenInference's. This test imports the real upstream constants
and asserts ours match value-for-value, so if either standard renames a key our copy fails
CI instead of drifting silently. Upstream is the single source of truth; core just restates
it where OTel cannot reach.
"""

from __future__ import annotations

import pytest
from agentship.observability import semconv


def test_gen_ai_keys_match_opentelemetry() -> None:
    """Every ``gen_ai.*`` constant equals OpenTelemetry's GenAI incubating attribute."""
    from opentelemetry.semconv._incubating.attributes import gen_ai_attributes as g

    assert semconv.GEN_AI_OPERATION_NAME == g.GEN_AI_OPERATION_NAME
    assert semconv.GEN_AI_SYSTEM == g.GEN_AI_SYSTEM
    assert semconv.GEN_AI_REQUEST_MODEL == g.GEN_AI_REQUEST_MODEL
    assert semconv.GEN_AI_RESPONSE_MODEL == g.GEN_AI_RESPONSE_MODEL
    assert semconv.GEN_AI_REQUEST_TEMPERATURE == g.GEN_AI_REQUEST_TEMPERATURE
    assert semconv.GEN_AI_REQUEST_MAX_TOKENS == g.GEN_AI_REQUEST_MAX_TOKENS
    assert semconv.GEN_AI_USAGE_INPUT_TOKENS == g.GEN_AI_USAGE_INPUT_TOKENS
    assert semconv.GEN_AI_USAGE_OUTPUT_TOKENS == g.GEN_AI_USAGE_OUTPUT_TOKENS
    assert semconv.GEN_AI_RESPONSE_FINISH_REASONS == g.GEN_AI_RESPONSE_FINISH_REASONS
    assert semconv.GEN_AI_TOOL_NAME == g.GEN_AI_TOOL_NAME
    assert semconv.GEN_AI_TOOL_CALL_ID == g.GEN_AI_TOOL_CALL_ID
    assert semconv.GEN_AI_INPUT_MESSAGES == g.GEN_AI_INPUT_MESSAGES
    assert semconv.GEN_AI_OUTPUT_MESSAGES == g.GEN_AI_OUTPUT_MESSAGES


def test_openinference_token_keys_match_upstream() -> None:
    """The ``llm.token_count.*`` mirror keys equal OpenInference's SpanAttributes.

    Skipped in a base install; runs under ``agentship-observability[phoenix]`` where the
    OpenInference conventions are present (they back Phoenix's cost panel).
    """
    trace = pytest.importorskip("openinference.semconv.trace")
    sa = trace.SpanAttributes

    assert semconv.OI_TOKEN_COUNT_PROMPT == sa.LLM_TOKEN_COUNT_PROMPT
    assert semconv.OI_TOKEN_COUNT_COMPLETION == sa.LLM_TOKEN_COUNT_COMPLETION
    assert semconv.OI_TOKEN_COUNT_TOTAL == sa.LLM_TOKEN_COUNT_TOTAL
