"""Opik exporter — OTLP/HTTP to an Opik backend (Comet's open-source LLM tracing tool) (§C4).

Opik is open-source and self-hostable, so the default endpoint is the local Opik server; it also
reads the GenAI/OpenInference attributes the observer stamps. As with Phoenix we reach it over the
**standard OTLP/HTTP exporter** rather than Opik's SDK, keeping the pipeline vendor-neutral — Opik
is just an OTLP endpoint here (integrate, don't reinvent).

The endpoint comes from ``OPIK_OTEL_ENDPOINT`` (default the local server). Auth is optional so the
local, keyless case just works: when ``OPIK_API_KEY`` is set an ``Authorization`` header is sent,
and ``OPIK_WORKSPACE`` / ``OPIK_PROJECT_NAME`` add the workspace and project headers Opik Cloud
expects. Pointing this at Opik Cloud sends spans off-box — the same SaaS consideration the
factory's ``allow_saas_exporter`` gate covers when the endpoint is a hosted one.
"""

from __future__ import annotations

import os

from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanProcessor

from ..config import ObservabilityConfig

#: Where a local self-hosted Opik server accepts OTLP/HTTP traces when nothing else is configured.
DEFAULT_ENDPOINT = "http://localhost:5173/api/v1/private/otel/v1/traces"


def build(config: ObservabilityConfig) -> SpanProcessor:
    """Build a batched OTLP/HTTP processor pointed at Opik, with optional auth/workspace headers.

    Reads ``OPIK_OTEL_ENDPOINT`` (optional) and, for Opik Cloud, the optional ``OPIK_API_KEY``,
    ``OPIK_WORKSPACE`` and ``OPIK_PROJECT_NAME``. With none of them set it targets a local Opik
    server with no auth, so the offline/self-hosted path needs no configuration.
    """
    endpoint = os.getenv("OPIK_OTEL_ENDPOINT", DEFAULT_ENDPOINT)
    headers: dict[str, str] = {}
    if api_key := os.getenv("OPIK_API_KEY"):
        headers["Authorization"] = api_key
    if workspace := os.getenv("OPIK_WORKSPACE"):
        headers["Comet-Workspace"] = workspace
    if project := os.getenv("OPIK_PROJECT_NAME"):
        headers["projectName"] = project
    return BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, headers=headers))
