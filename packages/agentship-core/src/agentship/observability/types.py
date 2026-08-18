"""The two vendor-free value types every observer speaks: :class:`SpanKind` and :class:`Usage`.

These carry no OpenTelemetry import on purpose. The ``Observer`` port, the frozen SEMCONV
constants, and these types all live in ``agentship-core`` so an engine hook (P12 eval, P13 audit)
can read the tracing contract *without* the exporter package installed (Phase 07 design §4.6). The
concrete OTel mapping of :class:`SpanKind` lives in ``agentship-observability``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class SpanKind(StrEnum):
    """The semantic role of a span, independent of the OTel ``trace.SpanKind`` enum.

    OpenTelemetry has no ``AGENT`` or ``TOOL`` kind, so we carry the role ourselves and let the
    exporter package map it (``AGENT``/``TOOL``/``INTERNAL`` → OTel ``INTERNAL``, ``LLM`` → OTel
    ``CLIENT``) while the *semantic* role rides on the ``gen_ai.operation.name`` attribute where
    Phoenix/Langfuse expect it (design §4.1). Keeping this a plain string enum means the span-tree
    contract is readable with zero OTel dependency.
    """

    AGENT = "agent"
    LLM = "llm"
    TOOL = "tool"
    INTERNAL = "internal"


@dataclass
class Usage:
    """One model call's cost/tokens/latency, handed to :meth:`Observer.on_model`.

    This is the uniform shape the LiteLLM callback (C3) produces for every provider, so a ``model``
    span carries the same keys whoever served it. ``cost_usd`` is ``None`` when the provider gave no
    price (a local model, say); ``response_model`` is the id the provider echoed back, which can
    differ from the requested one (an alias resolving to a dated snapshot), and defaults to ``None``
    when the provider did not report it.
    """

    #: The requested model id (a plain LiteLLM string, e.g. ``"openai/gpt-4o-mini"``).
    model: str
    #: The provider prefix LiteLLM resolved the call to, e.g. ``"openai"``/``"anthropic"``.
    provider: str
    #: Prompt tokens the provider billed.
    input_tokens: int
    #: Completion tokens the provider billed.
    output_tokens: int
    #: LiteLLM's computed dollar cost, or ``None`` when the provider reports no price.
    cost_usd: float | None
    #: Wall-clock duration of the call in milliseconds.
    latency_ms: float
    #: Provider finish reasons, e.g. ``["stop"]`` — a list since multi-choice replies each have one.
    finish_reasons: list[str] = field(default_factory=list)
    #: The model id the provider echoed back, when it differs from ``model``; ``None`` if absent.
    response_model: str | None = None
