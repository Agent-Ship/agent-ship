"""Wire-level proof: a real engine turn serializes the full nested trace over OTLP/HTTP (P07 DoD).

``test_tracing_engine_integration.py`` proves the tree reaches a vendor-free ``RecordingObserver``;
this proves the same turn survives the *real* export path — the production ``OTLPSpanExporter``
serializing to protobuf and POSTing to an OTLP/HTTP collector — which is the exact wire a hosted
Opik/LangFuse/LangSmith/Phoenix backend ingests. It stands up a throwaway in-process collector,
points a real exporter at it, runs an actual ``langgraph`` agent (tool-executing ReAct loop, fake
model so no network/key), then decodes the captured protobuf and asserts the bytes on the wire carry
agent → node → {model×2, tool.calculator}, with tokens and a priced cost on the model spans.

This is the keyless half of the live-backend proof: it needs no credentials yet exercises the same
serialization a real backend receives, so the hosted read-back (the P07 slice in ``agentship-demo``,
which owns the API keys) only has to confirm ingestion, not re-litigate that the exporter carries
everything.
"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

import agentship_langgraph.models as models_module
import pytest
from agentship.observability import semconv
from agentship.runtime import build_agent
from agentship.spec import AgentSpec
from agentship_observability import OTelObserver
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor


class _ToolThenAnswerModel(BaseChatModel):
    """Deterministic ReAct fake: first call requests the calculator, second answers — with usage."""

    calls: int = 0
    model: str = "openai/gpt-4o-mini"

    @property
    def _llm_type(self) -> str:
        """LangChain model-type tag (required by the base class)."""
        return "fake-tool-then-answer"

    @property
    def _identifying_params(self) -> dict[str, str]:
        """Surface the LiteLLM model id so the callback prices the call."""
        return {"model": self.model}

    def bind_tools(self, tools: object, **kwargs: object) -> BaseChatModel:
        """Accept the ReAct loop's tool binding and stay the same fake model."""
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        """Return a tool-call message first, then a final answer — both carrying token usage."""
        self.calls += 1
        if self.calls == 1:
            message = AIMessage(
                content="",
                tool_calls=[{"name": "calculator", "args": {"expression": "2 + 2"}, "id": "c1"}],
                usage_metadata={"input_tokens": 5, "output_tokens": 2, "total_tokens": 7},
            )
        else:
            message = AIMessage(
                content="The answer is 4.",
                usage_metadata={"input_tokens": 8, "output_tokens": 3, "total_tokens": 11},
            )
        return ChatResult(generations=[ChatGeneration(message=message)])


class _CollectorHandler(BaseHTTPRequestHandler):
    """Minimal OTLP/HTTP trace collector: stash each POSTed protobuf body, answer 200 empty."""

    bodies: list[bytes] = []

    def do_POST(self) -> None:  # noqa: N802 - name fixed by BaseHTTPRequestHandler
        """Capture one ``ExportTraceServiceRequest`` body and return an empty success response."""
        length = int(self.headers.get("Content-Length", "0"))
        _CollectorHandler.bodies.append(self.rfile.read(length))
        self.send_response(200)
        self.send_header("Content-Type", "application/x-protobuf")
        self.end_headers()
        self.wfile.write(b"")

    def log_message(self, *args: object) -> None:
        """Silence the default per-request stderr logging."""


def _decoded_spans(bodies: list[bytes]) -> list:
    """Decode captured OTLP bodies into a flat list of protobuf ``Span`` messages."""
    spans = []
    for body in bodies:
        request = ExportTraceServiceRequest()
        request.ParseFromString(body)
        for resource_spans in request.resource_spans:
            for scope_spans in resource_spans.scope_spans:
                spans.extend(scope_spans.spans)
    return spans


def _attr(span, key: str):
    """Pull one attribute's scalar value off a protobuf span, or None if absent."""
    for kv in span.attributes:
        if kv.key == key:
            value = kv.value
            if value.HasField("int_value"):
                return value.int_value
            if value.HasField("double_value"):
                return value.double_value
            if value.HasField("string_value"):
                return value.string_value
    return None


@pytest.fixture
def otlp_collector():
    """Run a throwaway OTLP/HTTP collector on a free port; yield its /v1/traces endpoint."""
    _CollectorHandler.bodies = []
    server = HTTPServer(("127.0.0.1", 0), _CollectorHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/v1/traces"
    finally:
        server.shutdown()
        thread.join(timeout=5)


async def test_full_trace_serializes_over_otlp_http(otlp_collector, monkeypatch):
    """A real engine turn exports agent → node → {model×2, tool} over OTLP with tokens and cost."""
    monkeypatch.setattr(models_module, "resolve_model", lambda *a, **k: _ToolThenAnswerModel())

    # Real exporter → in-process collector, SimpleSpanProcessor so every span ships on end.
    resource = Resource.create({SERVICE_NAME: "agentship-wire-proof"})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(SimpleSpanProcessor(OTLPSpanExporter(endpoint=otlp_collector)))
    observer = OTelObserver(provider)

    agent = build_agent(
        AgentSpec(
            name="wire",
            engine="langgraph",
            template="single",
            model="openai/gpt-4o-mini",
            prompt="You do math.",
            tools=["calculator"],
        ),
        observer=observer,
    )

    result = await agent.run("What is 2 + 2?")
    assert "4" in result.output
    provider.force_flush()

    spans = _decoded_spans(_CollectorHandler.bodies)
    names = sorted(span.name for span in spans)

    assert semconv.SPAN_AGENT in names, "the root agent span must reach the wire"
    assert names.count(semconv.SPAN_MODEL) == 2, "both ReAct model calls must serialize"
    assert semconv.tool_span("calculator") in names, "the tool span must serialize"
    assert any(n.startswith(semconv.NODE_PREFIX) for n in names), "the node boundary must serialize"

    model_spans = [s for s in spans if s.name == semconv.SPAN_MODEL]
    first = model_spans[0]
    assert _attr(first, semconv.GEN_AI_USAGE_INPUT_TOKENS) == 5
    assert _attr(first, semconv.GEN_AI_USAGE_OUTPUT_TOKENS) == 2
    assert _attr(first, semconv.AS_COST_USD) > 0, "LiteLLM should price the model on the wire"
    assert _attr(first, semconv.GEN_AI_REQUEST_MODEL) == "openai/gpt-4o-mini"

    # Nesting survives serialization: every non-root span carries a parent, and exactly one root.
    ids = {span.span_id for span in spans}
    roots = [s for s in spans if not s.parent_span_id or s.parent_span_id not in ids]
    assert len(roots) == 1 and roots[0].name == semconv.SPAN_AGENT
