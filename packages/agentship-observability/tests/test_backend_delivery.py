"""Every named backend really ships a span — over the wire, with the right path and headers.

The claim "works across Phoenix, Langfuse, LangSmith and Opik" rested on read-back tests that are
all ``live_only`` and skip in CI. So the claim was asserted and never verified: an exporter could
build a wrong URL, drop its auth header, or POST to the wrong path, and nothing would notice until
someone with credentials looked at an empty dashboard and had no idea which of the four layers had
failed.

What can be proven without credentials is everything up to the vendor's front door: that the
exporter the factory builds for each backend **does POST**, to the endpoint that backend
configures, carrying the auth and routing headers that backend needs. That is the part we own.
Whether the vendor then accepts the payload is theirs, and stays a live check.

Each case points the backend's own environment variable at a throwaway in-process collector, so
what is exercised is the real ``build()`` for that backend — not a stand-in that happens to agree
with it.
"""

from __future__ import annotations

import base64
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

import pytest
from agentship.observability import SpanKind
from agentship_observability.config import ObservabilityConfig
from agentship_observability.exporters.factory import build_processor
from agentship_observability.otel.observer import OTelObserver
from opentelemetry.sdk.trace import TracerProvider


class _Collector(BaseHTTPRequestHandler):
    """Stand in for a vendor's OTLP endpoint: record the request, answer 200."""

    #: (path, headers, body length) for each POST that arrived, across all instances.
    received: list[tuple[str, dict, int]] = []

    def do_POST(self) -> None:  # noqa: N802 - name fixed by BaseHTTPRequestHandler
        """Record one export request and acknowledge it."""
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        _Collector.received.append((self.path, dict(self.headers), length))
        self.send_response(200)
        self.send_header("Content-Type", "application/x-protobuf")
        self.end_headers()
        self.wfile.write(b"")

    def log_message(self, *args: object) -> None:
        """Silence the default per-request stderr logging."""


def _header(headers: dict, name: str) -> str | None:
    """Look a header up case-insensitively, the way HTTP defines them.

    Exporters disagree about casing — Langfuse sends ``Authorization``, LangSmith sends
    ``x-api-key`` — and matching exactly would fail an exporter that is behaving correctly.
    """
    lowered = {key.lower(): value for key, value in headers.items()}
    return lowered.get(name.lower())


@pytest.fixture
def collector():
    """Run a throwaway OTLP collector and yield its base URL."""
    _Collector.received.clear()
    server = HTTPServer(("127.0.0.1", 0), _Collector)
    Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()


def _emit_one_span(processor) -> None:
    """Send a single span through ``processor`` and flush, the way a finished turn would.

    Flushed explicitly because every backend uses a *batching* processor: without this the test
    would end before the background thread exported anything and would pass for an exporter that
    never sends at all — which is precisely the failure being ruled out.
    """
    provider = TracerProvider()
    provider.add_span_processor(processor)
    observer = OTelObserver(provider)
    with observer.span("agent delivery-check", SpanKind.AGENT, {"agentship.agent.name": "probe"}):
        pass
    provider.force_flush()
    provider.shutdown()


#: Each backend: the env var that points it somewhere, the path it must POST to, and a header it
#: must carry. The header is the half a local collector can still check — an exporter that drops
#: its credential reaches the endpoint and is rejected by the vendor, which looks identical to
#: "not configured" from the outside.
_BACKENDS = [
    pytest.param("phoenix", "PHOENIX_COLLECTOR_ENDPOINT", "/v1/traces", None, id="phoenix"),
    pytest.param("opik", "OPIK_OTEL_ENDPOINT", "", None, id="opik"),
    pytest.param("langsmith", "LANGSMITH_OTEL_ENDPOINT", "", "x-api-key", id="langsmith"),
    pytest.param(
        "langfuse", "LANGFUSE_HOST", "/api/public/otel/v1/traces", "authorization", id="langfuse"
    ),
]


@pytest.mark.parametrize(("name", "env_var", "path", "auth_header"), _BACKENDS)
def test_the_backend_exporter_actually_posts_a_span(
    name, env_var, path, auth_header, collector, monkeypatch
) -> None:
    """A span reaches the endpoint this backend is configured with, carrying its credential.

    This is the CI-runnable half of "works across all four backends": delivery through the real
    exporter, to the real configured URL, with the real headers. The vendor accepting it remains
    a live check — but an empty dashboard can now be attributed, because this rules out the
    three layers underneath it.
    """
    monkeypatch.setenv(env_var, f"{collector}{path}" if name != "langfuse" else collector)
    monkeypatch.setenv("LANGSMITH_API_KEY", "ls-test-key")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    monkeypatch.setenv("OPIK_API_KEY", "opik-test")

    processor = build_processor(name, ObservabilityConfig(provider="otel", exporters=[name]))
    assert processor is not None, f"{name} built no processor"
    _emit_one_span(processor)

    assert _Collector.received, f"{name} exported nothing — the span never left the process"
    got_path, headers, length = _Collector.received[0]
    assert length > 0, "an empty body is not an exported span"
    if path:
        assert got_path == path, f"{name} posted to {got_path!r}, not its documented {path!r}"
    if auth_header:
        assert _header(headers, auth_header), (
            f"{name} exported without its {auth_header} header — the vendor would reject this, "
            f"and from the outside that is indistinguishable from tracing being switched off"
        )


def test_langfuse_sends_basic_auth_built_from_both_keys(collector, monkeypatch) -> None:
    """Langfuse authenticates with the key PAIR, base64'd — not either key alone.

    Worth pinning separately: the two keys are assembled by hand into one header, so a swap or a
    missing colon produces a header that looks plausible, exports cleanly, and is rejected at the
    far end with nothing local to show for it.
    """
    monkeypatch.setenv("LANGFUSE_HOST", collector)
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-abc")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-xyz")

    _emit_one_span(
        build_processor("langfuse", ObservabilityConfig(provider="otel", exporters=["langfuse"]))
    )

    _path, headers, _length = _Collector.received[0]
    scheme, _, encoded = _header(headers, "authorization").partition(" ")
    assert scheme == "Basic"
    assert base64.b64decode(encoded).decode() == "pk-abc:sk-xyz"
