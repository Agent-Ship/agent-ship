# agentship-observability

The OpenTelemetry implementation of AgentShip's `Observer` port (Phase 07).

The **contract** — the `Observer`/`Span` port, `SpanKind`/`Usage`, the frozen SEMCONV span-name and
attribute-key constants, and the `TraceView` read-port — lives in **`agentship-core`** with no OTel
dependency, so an engine or eval hook can read a trace with only the kernel installed (design §4.6).

This package holds only the **pipeline**:

- `otel/observer.py` — `OTelObserver`, the concrete port over a `TracerProvider`: it builds the
  canonical span tree (§4.1), sets GenAI attributes (§4.2), and rolls up cost/tokens/latency.
- `exporters/` — one `SpanProcessor` per backend (`console`, `phoenix`, `langfuse`, `langsmith`),
  all reached over OTLP/HTTP (no per-vendor SDK lock-in).
- `litellm_callback.py` — one process-global `CustomLogger` that stamps cost/tokens/latency onto the
  current `model` span, so every LiteLLM caller is covered without per-call wiring.
- `config.py` + `factory.py` — the `observability:` config surface and the process-global
  `TracerProvider` the factory memoises; `provider: otel` (default) → `OTelObserver`, `none` → NoOp.
- `capture.py` — the record/replay hook that stamps `agentship.replay.request_hash` (+ gated
  request/response) so P12 can build deterministic cassettes.
- `studio.py` — `generate_langgraph_json` + the loopback-only, dev-token studio launcher.

Everything a price table, trace store, or UI would provide is **consumed** (Phoenix/Langfuse/
LangSmith + LangGraph Studio); we build only the composition.
