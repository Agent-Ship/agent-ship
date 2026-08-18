"""Map AgentShip's semantic :class:`SpanKind` onto OpenTelemetry's ``trace.SpanKind`` (§4.1).

OTel has no ``AGENT`` or ``TOOL`` kind, so the semantic role travels on the
``gen_ai.operation.name`` attribute (where Phoenix/Langfuse's OpenInference expects it) and the OTel
kind is only a coarse client/internal hint. The mapping: ``LLM`` → ``CLIENT`` (an outbound call to a
provider), everything else (``AGENT``/``TOOL``/``INTERNAL``) → ``INTERNAL``. Kept in one tiny table
so the choice is inspectable in one place.
"""

from __future__ import annotations

from agentship.observability import SpanKind
from opentelemetry.trace import SpanKind as OTelSpanKind

#: The single source of truth for AgentShip-kind → OTel-kind. ``LLM`` is the only outbound call.
_KIND_TO_OTEL: dict[SpanKind, OTelSpanKind] = {
    SpanKind.AGENT: OTelSpanKind.INTERNAL,
    SpanKind.LLM: OTelSpanKind.CLIENT,
    SpanKind.TOOL: OTelSpanKind.INTERNAL,
    SpanKind.INTERNAL: OTelSpanKind.INTERNAL,
}


def to_otel_kind(kind: SpanKind) -> OTelSpanKind:
    """Return the OTel ``SpanKind`` for an AgentShip ``SpanKind`` (defaults to ``INTERNAL``)."""
    return _KIND_TO_OTEL.get(kind, OTelSpanKind.INTERNAL)
