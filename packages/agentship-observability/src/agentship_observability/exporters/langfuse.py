"""Langfuse exporter — OTLP/HTTP to a Langfuse project (§C4).

Langfuse (self-hostable or SaaS) accepts OTLP at ``${LANGFUSE_HOST}/api/public/otel/v1/traces`` and
authenticates with HTTP Basic over the project's public/secret key pair. Keys and host come from the
environment (never the config file) so a secret never lands in checked-in YAML.
"""

from __future__ import annotations

import base64
import os

from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanProcessor

from ..config import ObservabilityConfig

#: Langfuse Cloud host used when ``LANGFUSE_HOST`` is unset; self-hosters override it.
DEFAULT_HOST = "https://cloud.langfuse.com"
#: OTLP traces path appended to the host.
OTEL_PATH = "/api/public/otel/v1/traces"


def _basic_auth_header(public_key: str, secret_key: str) -> str:
    """Return the ``Basic <b64>`` value for Langfuse's public/secret key pair."""
    token = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
    return f"Basic {token}"


def build(config: ObservabilityConfig) -> SpanProcessor:
    """Build a batched OTLP/HTTP processor pointed at Langfuse with Basic-auth headers.

    Reads ``LANGFUSE_HOST`` (optional), ``LANGFUSE_PUBLIC_KEY`` and ``LANGFUSE_SECRET_KEY`` from the
    environment. Missing keys still build a processor (fail-open) — the backend simply rejects the
    unauthenticated export rather than breaking the agent run.
    """
    host = os.getenv("LANGFUSE_HOST", DEFAULT_HOST).rstrip("/")
    public_key = os.getenv("LANGFUSE_PUBLIC_KEY", "")
    secret_key = os.getenv("LANGFUSE_SECRET_KEY", "")
    headers = {"Authorization": _basic_auth_header(public_key, secret_key)}
    return BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{host}{OTEL_PATH}", headers=headers))
