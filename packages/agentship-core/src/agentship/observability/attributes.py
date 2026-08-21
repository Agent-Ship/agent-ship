"""Map a :class:`Usage` roll-up onto the frozen model-span attribute keys (§4.2).

One function, :func:`usage_attributes`, turns the vendor-neutral :class:`~agentship.observability.
types.Usage` a model call produces into the exact ``gen_ai.*`` / ``llm.token_count.*`` /
``agentship.*`` keys a model span carries. It lives here, in the kernel, so **every** writer of a
model span shares one mapping and cannot drift: :meth:`Observer.on_model` (current-span path) and
the LangGraph tracing callback (which holds the model span directly and stamps it) both call this.
Keeping the token/cost/latency contract in a single place is what the parity guard protects.
"""

from __future__ import annotations

from typing import Any

from .semconv import (
    AS_COST_USD,
    AS_LATENCY_MS,
    GEN_AI_RESPONSE_FINISH_REASONS,
    GEN_AI_RESPONSE_MODEL,
    GEN_AI_SYSTEM,
    GEN_AI_USAGE_INPUT_TOKENS,
    GEN_AI_USAGE_OUTPUT_TOKENS,
    OI_TOKEN_COUNT_COMPLETION,
    OI_TOKEN_COUNT_PROMPT,
    OI_TOKEN_COUNT_TOTAL,
)
from .types import Usage


def usage_attributes(usage: Usage) -> dict[str, Any]:
    """Return the model-span attributes for one :class:`Usage` roll-up.

    Emits the GenAI token counts (also mirrored to the OpenInference ``llm.token_count.*`` keys
    Phoenix's cost panel reads), the provider, finish reasons and latency. ``cost_usd`` and
    ``response_model`` are included only when the provider reported them, so an absent price or
    echoed model id leaves the key off rather than writing a misleading ``None``/zero.
    """
    attrs: dict[str, Any] = {
        GEN_AI_SYSTEM: usage.provider,
        GEN_AI_USAGE_INPUT_TOKENS: usage.input_tokens,
        GEN_AI_USAGE_OUTPUT_TOKENS: usage.output_tokens,
        GEN_AI_RESPONSE_FINISH_REASONS: list(usage.finish_reasons),
        OI_TOKEN_COUNT_PROMPT: usage.input_tokens,
        OI_TOKEN_COUNT_COMPLETION: usage.output_tokens,
        OI_TOKEN_COUNT_TOTAL: usage.input_tokens + usage.output_tokens,
        AS_LATENCY_MS: usage.latency_ms,
    }
    if usage.cost_usd is not None:
        attrs[AS_COST_USD] = usage.cost_usd
    if usage.response_model is not None:
        attrs[GEN_AI_RESPONSE_MODEL] = usage.response_model
    return attrs
