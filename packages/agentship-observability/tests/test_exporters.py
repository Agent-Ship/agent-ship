"""Exporter builders resolve the right endpoints and auth headers (P07 · C4).

Each builder reads its endpoint/keys from the environment and returns an OTel ``SpanProcessor``
wrapping an ``OTLPSpanExporter``. These tests reach through the processor to the exporter's
``_endpoint``/``_headers`` to prove the config maps to the right wire target — without any network.
"""

from __future__ import annotations

import base64

import pytest
from agentship.errors import CapabilityError
from agentship_observability.config import ObservabilityConfig
from agentship_observability.exporters import build_processor
from agentship_observability.exporters import phoenix as phoenix_mod


def _exporter(processor):
    """Reach the underlying span exporter through a processor (attr name varies by version)."""
    return getattr(processor, "span_exporter", None) or processor._exporter


def test_console_needs_no_backend() -> None:
    """The console exporter builds with no env and no endpoint — the offline-safe default."""
    processor = build_processor("console", ObservabilityConfig())
    assert processor is not None


def test_phoenix_uses_env_endpoint(monkeypatch) -> None:
    """Phoenix points at ``PHOENIX_COLLECTOR_ENDPOINT`` when set, else the local default."""
    monkeypatch.delenv("PHOENIX_COLLECTOR_ENDPOINT", raising=False)
    default = _exporter(build_processor("phoenix", ObservabilityConfig()))
    assert default._endpoint == phoenix_mod.DEFAULT_ENDPOINT

    monkeypatch.setenv("PHOENIX_COLLECTOR_ENDPOINT", "http://phoenix.internal:6006/v1/traces")
    custom = _exporter(build_processor("phoenix", ObservabilityConfig()))
    assert custom._endpoint == "http://phoenix.internal:6006/v1/traces"


def test_langfuse_builds_endpoint_and_basic_auth(monkeypatch) -> None:
    """Langfuse targets ``${host}/api/public/otel/v1/traces`` with a Basic-auth header from keys."""
    monkeypatch.setenv("LANGFUSE_HOST", "https://lf.example.com")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-1")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-2")
    exporter = _exporter(build_processor("langfuse", ObservabilityConfig()))
    assert exporter._endpoint == "https://lf.example.com/api/public/otel/v1/traces"
    expected = "Basic " + base64.b64encode(b"pk-1:sk-2").decode()
    assert exporter._headers["Authorization"] == expected


def test_langsmith_builds_endpoint_and_api_key_header(monkeypatch) -> None:
    """LangSmith targets its OTLP endpoint with an ``x-api-key`` header from the env."""
    monkeypatch.delenv("LANGSMITH_OTEL_ENDPOINT", raising=False)
    monkeypatch.setenv("LANGSMITH_API_KEY", "ls-key")
    monkeypatch.delenv("LANGSMITH_PROJECT", raising=False)
    exporter = _exporter(build_processor("langsmith", ObservabilityConfig()))
    assert exporter._endpoint.endswith("/otel/v1/traces")
    assert exporter._headers["x-api-key"] == "ls-key"


def test_unknown_exporter_raises_capability_error() -> None:
    """An unregistered exporter name fails fast, listing the known names."""
    with pytest.raises(CapabilityError) as excinfo:
        build_processor("splunk", ObservabilityConfig())
    assert "splunk" in str(excinfo.value)
    assert "console" in str(excinfo.value)
