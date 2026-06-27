"""OTEL tracing wired to Langfuse (or stdout) based on agent config."""

from __future__ import annotations

import base64
import logging
import os
from contextlib import contextmanager
from typing import Any, Optional

logger = logging.getLogger(__name__)

_TRACER_PROVIDER = None
_TRACER = None


def setup(agent_name: str, backend: str = "none", **kwargs: Any) -> None:
    """Configure the global OTEL tracer provider once per process."""
    global _TRACER_PROVIDER, _TRACER

    if _TRACER_PROVIDER is not None:
        return  # Already configured

    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

    provider = TracerProvider()

    if backend == "langfuse":
        exporter = _langfuse_exporter(**kwargs)
        if exporter:
            provider.add_span_processor(BatchSpanProcessor(exporter))
            logger.info("OTEL → Langfuse configured for agent '%s'", agent_name)
        else:
            logger.warning("Langfuse OTEL exporter failed to initialize; falling back to console")
            provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    elif backend == "console":
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
        logger.info("OTEL → console for agent '%s'", agent_name)
    else:
        # No-op: no processor means spans are created but immediately discarded
        pass

    trace.set_tracer_provider(provider)
    _TRACER_PROVIDER = provider
    _TRACER = trace.get_tracer(agent_name)


def _langfuse_exporter(endpoint: Optional[str] = None, public_key: Optional[str] = None, secret_key: Optional[str] = None, **_: Any):
    """Build an OTLP exporter pointed at Langfuse's OTEL endpoint."""
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        ep = endpoint or os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com")
        pk = public_key or os.environ.get("LANGFUSE_PUBLIC_KEY", "")
        sk = secret_key or os.environ.get("LANGFUSE_SECRET_KEY", "")

        if not pk or not sk:
            logger.warning("LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY not set; Langfuse disabled")
            return None

        credentials = base64.b64encode(f"{pk}:{sk}".encode()).decode()
        return OTLPSpanExporter(
            endpoint=f"{ep.rstrip('/')}/api/public/otel/v1/traces",
            headers={"Authorization": f"Basic {credentials}"},
        )
    except ImportError:
        logger.warning("opentelemetry-exporter-otlp not installed; install it to enable Langfuse")
        return None


@contextmanager
def span(name: str, **attrs: Any):
    """Context manager that creates an OTEL span if a tracer is configured."""
    if _TRACER is None:
        yield None
        return

    with _TRACER.start_as_current_span(name) as s:
        for k, v in attrs.items():
            s.set_attribute(k, str(v))
        try:
            yield s
        except Exception as exc:
            s.record_exception(exc)
            raise


def get_tracer():
    return _TRACER
