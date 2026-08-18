"""Console exporter — prints finished spans to stderr. The zero-backend default (§C4).

Needs nothing running, so it is the safe default: a dev can see the span tree immediately and an
offline CI run never fails on a missing collector. Uses a ``SimpleSpanProcessor`` (synchronous
export) because console output is cheap and immediate feedback beats batching here.
"""

from __future__ import annotations

from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor, SpanProcessor

from ..config import ObservabilityConfig


def build(config: ObservabilityConfig) -> SpanProcessor:
    """Build a synchronous processor that prints each finished span to stderr."""
    return SimpleSpanProcessor(ConsoleSpanExporter())
