"""AgentShip observability — the OpenTelemetry pipeline behind the vendor-free ``Observer`` port.

The contract (port, ``SpanKind``/``Usage``, SEMCONV, ``TraceView``) lives in ``agentship-core``;
this package supplies the concrete OTel ``OTelObserver``, the
exporter pipeline, the one-per-process LiteLLM cost/token callback, the record/replay capture hook,
and the studio launcher (design §4.6). Import ``OTelObserver`` here; build a configured one with
:func:`~agentship_observability.factory.build_otel_observer`.
"""

from __future__ import annotations

from .otel import OTelObserver

__all__ = ["OTelObserver"]
