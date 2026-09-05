# AgentShip Observability — Semantic Conventions (frozen contract)

**Version:** `0.1.0` · **Owner:** Phase 07 (Observability) · **Consumers:** P12 (evals), P13 (audit)

This is the human-readable golden copy of the tracing contract. Every span name and attribute key
AgentShip emits is a **public, versioned contract**: downstream phases read a trace by these exact
strings, so they must not drift silently. The machine-readable constants live in
`agentship.observability.semconv` (importable from **`agentship-core`** with
`agentship-observability` uninstalled). This file and that module must stay in lock-step — the
`CONF-OBS-3` / `CONF-OBS-6` conformance cells enforce it.

The `gen_ai.*` keys are **OpenTelemetry's** GenAI semantic conventions and the `llm.token_count.*`
keys are **OpenInference's** — we adopt the standards, we do not invent them. A drift guard
(`agentship-observability/tests/test_semconv_upstream.py`) imports the real upstream constants and
fails CI if any value diverges. Only the `agentship.*` (`AS_*`) keys and the `SPAN_*` names are ours.

Bump `SEMCONV_VERSION` only with a documented change here.

## 1. Span tree (§4.1)

One `agent` root per interaction; everything else nests under it. `SpanKind` is AgentShip's semantic
kind; the OTel kind is a coarse client/internal hint (`LLM → CLIENT`, everything else `INTERNAL`) —
the semantic role travels on `gen_ai.operation.name`, not the OTel kind.

```
agent                     (AGENT)     — root, one per run
├── guardrail.input       (INTERNAL)
├── memory.recall         (INTERNAL)
├── node.<name>           (INTERNAL)  — repeats per graph node / step
│   ├── model             (LLM)       — one per model call; usage stamped here
│   └── tool.<name>       (TOOL)      — one per tool invocation
├── output.validate       (INTERNAL)
├── guardrail.output      (INTERNAL)
└── memory.write          (INTERNAL)
```

| Span name | Constant | Kind |
|---|---|---|
| `agent` | `SPAN_AGENT` | AGENT |
| `model` | `SPAN_MODEL` | LLM |
| `guardrail.input` | `SPAN_GUARDRAIL_INPUT` | INTERNAL |
| `guardrail.output` | `SPAN_GUARDRAIL_OUTPUT` | INTERNAL |
| `memory.recall` | `SPAN_MEMORY_RECALL` | INTERNAL |
| `memory.write` | `SPAN_MEMORY_WRITE` | INTERNAL |
| `output.validate` | `SPAN_OUTPUT_VALIDATE` | INTERNAL |
| `node.<name>` | `node_span(name)` (`NODE_PREFIX`) | INTERNAL |
| `tool.<name>` | `tool_span(name)` (`TOOL_PREFIX`) | TOOL |

## 2. Attribute keys (§4.2)

### GenAI semantic conventions (`gen_ai.*`)

| Key | Constant | Where |
|---|---|---|
| `gen_ai.operation.name` | `GEN_AI_OPERATION_NAME` | model / tool |
| `gen_ai.system` | `GEN_AI_SYSTEM` | model |
| `gen_ai.request.model` | `GEN_AI_REQUEST_MODEL` | model |
| `gen_ai.response.model` | `GEN_AI_RESPONSE_MODEL` | model |
| `gen_ai.request.temperature` | `GEN_AI_REQUEST_TEMPERATURE` | model |
| `gen_ai.request.max_tokens` | `GEN_AI_REQUEST_MAX_TOKENS` | model |
| `gen_ai.usage.input_tokens` | `GEN_AI_USAGE_INPUT_TOKENS` | model |
| `gen_ai.usage.output_tokens` | `GEN_AI_USAGE_OUTPUT_TOKENS` | model |
| `gen_ai.response.finish_reasons` | `GEN_AI_RESPONSE_FINISH_REASONS` | model |
| `gen_ai.tool.name` | `GEN_AI_TOOL_NAME` | tool |
| `gen_ai.tool.call.id` | `GEN_AI_TOOL_CALL_ID` | tool |
| `gen_ai.input.messages` | `GEN_AI_INPUT_MESSAGES` | model — **gated** (§4) |
| `gen_ai.output.messages` | `GEN_AI_OUTPUT_MESSAGES` | model — **gated** (§4) |

### OpenInference mirror (Phoenix cost panel)

| Key | Constant |
|---|---|
| `llm.token_count.prompt` | `OI_TOKEN_COUNT_PROMPT` |
| `llm.token_count.completion` | `OI_TOKEN_COUNT_COMPLETION` |
| `llm.token_count.total` | `OI_TOKEN_COUNT_TOTAL` |

### AgentShip-owned (`agentship.*`)

| Key | Constant | Where |
|---|---|---|
| `agentship.cost.usd` | `AS_COST_USD` | model (nullable) |
| `agentship.latency.ms` | `AS_LATENCY_MS` | model |
| `agentship.status` | `AS_STATUS` | root — `ok` \| `error` |
| `agentship.run.mode` | `AS_RUN_MODE` | root — `invoke` \| `stream` |
| `agentship.tenant.id` | `AS_TENANT_ID` | root |
| `agentship.session.id` | `AS_SESSION_ID` | root |
| `agentship.run.id` | `AS_RUN_ID` | root |
| `agentship.agent.name` | `AS_AGENT_NAME` | root |
| `agentship.user.id` | `AS_USER_ID` | root — **salted hash only** (§4) |
| `agentship.tool.idempotent` | `AS_TOOL_IDEMPOTENT` | tool |
| `agentship.tool.mcp_server` | `AS_TOOL_MCP_SERVER` | tool |
| `agentship.replay.request_hash` | `AS_REPLAY_REQUEST_HASH` | model (§5) |

## 3. `TraceView` read-port (§4.9)

The finished span tree exposed read-only, backend-agnostic, built from the in-process capture so
verifiers run offline. **`SpanNode` is frozen** (P12's `Verifier` depends on the shape):

```python
@dataclass(frozen=True)
class SpanNode:
    name: str  # a §1 span name, e.g. "model", "tool.search"
    kind: SpanKind
    attrs: Mapping[str, Any]  # the §2 keys
    status: str  # "ok" | "error"
    children: tuple["SpanNode", ...]


class TraceView:
    root: SpanNode

    def spans(self, name: str | None = None) -> Iterable[SpanNode]: ...
    def model_spans(self) -> Iterable[SpanNode]: ...  # name == "model"
    def tool_calls(self) -> Iterable[SpanNode]: ...  # name startswith "tool."
```

## 4. PHI gate (§4.6)

- **`capture_content` (default `false`).** When false, `gen_ai.input.messages` /
  `gen_ai.output.messages`, tool args/results, and memory content are **never** set as attributes.
- **`hash_user_id` (default `true`).** `agentship.user.id` is the salted, truncated SHA-256 of the
  caller's `user_id` (`hashed_user_id`, 16 hex chars), never the raw id. Salt from
  `AGENTSHIP_HASH_SALT`.
- **SaaS-exporter gate.** `langsmith` (SaaS) is refused unless `allow_saas_exporter=true`; Phoenix
  and Langfuse are self-hostable and PHI-eligible. Enforced once, on the shared export path (runtime
  **and** eval-export).

## 5. Record/replay capture (§4.10)

Every `model` span carries `agentship.replay.request_hash` — the SHA-256 of the canonicalized
request (`model` + `messages` + params). Identical requests hash equal; any output-affecting change
differs. The request/response payload (`gen_ai.input.messages` / `gen_ai.output.messages`) is
recorded only under the `capture_content` gate. P12 keys LLM cassettes on the hash for
`origin="trace"` replay.
