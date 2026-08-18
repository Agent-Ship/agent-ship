"""Phoenix exporter — OTLP/HTTP to an Arize Phoenix collector. The recommended OSS backend (§C4).

Phoenix is self-hosted and open-source, reads the GenAI + OpenInference attributes the observer
stamps, and needs no API key — just a running collector. The endpoint comes from
``PHOENIX_COLLECTOR_ENDPOINT`` (default the local dev collector), so pointing at a shared Phoenix is
an env-var change, not a code change.
"""

from __future__ import annotations

import os

from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanProcessor

from ..config import ObservabilityConfig

#: Where Phoenix listens for OTLP/HTTP traces when nothing else is configured (local dev collector).
DEFAULT_ENDPOINT = "http://localhost:6006/v1/traces"


def build(config: ObservabilityConfig) -> SpanProcessor:
    """Build a batched OTLP/HTTP processor pointed at the Phoenix collector."""
    endpoint = os.getenv("PHOENIX_COLLECTOR_ENDPOINT", DEFAULT_ENDPOINT)
    return BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint))
