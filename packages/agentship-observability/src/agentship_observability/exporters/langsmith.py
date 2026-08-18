"""LangSmith exporter — OTLP/HTTP to LangSmith. The one SaaS backend, gated (§C4, §4.6).

LangSmith is a hosted service, so spans leave the boundary. The factory only wires it in when the
config's ``allow_saas_exporter`` is set — this module just builds the processor. Endpoint and
``x-api-key`` come from the environment; an optional ``LANGSMITH_PROJECT`` tags the traces.
"""

from __future__ import annotations

import os

from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanProcessor

from ..config import ObservabilityConfig

#: LangSmith's hosted OTLP traces endpoint when ``LANGSMITH_OTEL_ENDPOINT`` is unset.
DEFAULT_ENDPOINT = "https://api.smith.langchain.com/otel/v1/traces"


def build(config: ObservabilityConfig) -> SpanProcessor:
    """Build a batched OTLP/HTTP processor pointed at LangSmith with its ``x-api-key`` header.

    Reads ``LANGSMITH_OTEL_ENDPOINT`` (optional), ``LANGSMITH_API_KEY`` and ``LANGSMITH_PROJECT``
    (optional) from the environment. The SaaS gate lives in the factory, not here.
    """
    endpoint = os.getenv("LANGSMITH_OTEL_ENDPOINT", DEFAULT_ENDPOINT)
    headers = {"x-api-key": os.getenv("LANGSMITH_API_KEY", "")}
    project = os.getenv("LANGSMITH_PROJECT")
    if project:
        headers["Langsmith-Project"] = project
    return BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, headers=headers))
