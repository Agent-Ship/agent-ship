# Observability

Every turn of any AgentShip agent emits one OpenTelemetry trace tree — agent →
node → model → tool, with tokens, cost, and latency — turned on by a single YAML
block and exportable to Phoenix (or Langfuse / LangSmith / Opik) with no code
change.

## What it is

- **A vendor-free `Observer` seam.** The kernel calls one tiny seam —
  `agentship.observability.Observer` (`span()` / `start_span()` / `on_model()` /
  `current_trace_id()`) — and it never imports OpenTelemetry. The contract, the
  `SpanKind` / `Usage` value types, and the frozen `semconv` keys all live in
  `agentship-core`, so an engine or eval hook reads the tracing contract with
  nothing but the kernel installed. The concrete OTel implementation lives
  behind the seam in the separate `agentship-observability` package.
- **The full trace tree.** One interaction is one trace: a root `agent` span
  (`SPAN_AGENT`) wrapping `node.<name>` → `model` → `tool.<name>` children, plus
  `guardrail.*` / `memory.*` spans. Each `model` span carries the OTel GenAI
  attributes (`gen_ai.usage.input_tokens`, `gen_ai.request.model`, …) plus
  AgentShip's `agentship.cost.usd` / `agentship.latency.ms`.
- **OTLP/HTTP export, config-swap only.** Exporters (`console`, `phoenix`,
  `langfuse`, `langsmith`, `opik`) each build an OTel `SpanProcessor`; Phoenix is
  the recommended OSS backend, reached over the standard OTLP/HTTP exporter (no
  Phoenix-SDK lock-in). Swap backends by editing one `exporters:` line.
- **An offline `RecordingObserver`.** `agentship.observability.RecordingObserver`
  captures the same span tree in memory — no collector, no OTel — and exposes it
  as a frozen `TraceView` of `SpanNode`s, so tests and P12 verifiers assert on
  the tree the run *should* have built.
- **Guarded against upstream drift.** `test_semconv_upstream.py` asserts our
  `gen_ai.*` / `llm.*` strings equal the real OpenTelemetry-GenAI / OpenInference
  constants; `test_observer_parity.py` runs one interaction through both the
  `RecordingObserver` and the real OTel observer and asserts the trees agree.

## How to use it

Turn tracing on with an `observability:` block — the runtime resolves it to a
real observer, no code required:

```yaml
# assistant.yaml
name: assistant
engine: langgraph
model: openai/gpt-4o-mini
observability:
  provider: otel
  exporters: [phoenix]      # console | phoenix | langfuse | langsmith | opik
  capture_content: false    # PHI gate — content never leaves the process
```

```python
from agentship import build_agent

agent = build_agent("assistant.yaml")   # observability: block → live OTel observer
result = await agent.run("What is 21 * 2? Use the calculator.")
print(result.output)
```

Point Phoenix at a collector with `PHOENIX_COLLECTOR_ENDPOINT` (defaults to
`http://localhost:6006/v1/traces`) — an env change, not a code change. To build
an observer directly, call
`agentship_observability.factory.build_otel_observer(config)`.

Assert on the trace tree offline, with no collector, using `RecordingObserver`:

```python
from agentship.observability import RecordingObserver, SpanKind

observer = RecordingObserver()
with observer.span("agent", SpanKind.AGENT):
    with observer.span("node.solve", SpanKind.INTERNAL):
        with observer.span("model", SpanKind.LLM):
            pass

trace = observer.trace_view()                 # frozen TraceView
assert trace.root.name == "agent"
assert [s.name for s in trace.model_spans()] == ["model"]
```

## One runnable example

`agentship/examples/observability.yaml` — the zero-dependency `echo` engine with
an `observability: {provider: otel, exporters: [console]}` block; run
`agentship run examples/observability.yaml --input "hi"` and the root `agent`
span prints to stderr while stdout stays the clean answer. For the full
agent → node → model → tool tree on a live tool-calling turn, see
`agentship-demo/demos/observability.py` (needs `OPENAI_API_KEY`).

## Status & limits

✅ delivered 2026-08-18 (C1–C6 + six `CONF-OBS-*` conformance cells green). Known
open item on `STATUS.md`: model/tool/MCP/graph-node child-span wiring
completeness — MCP tool tagging is only unit-tested in isolation, so a
real-MCP-server full-trace-tree test (agent → node → `tool.<server>` emitted and
read back) is the remaining proof. Authoritative status: `.spec-dev/STATUS.md`.
