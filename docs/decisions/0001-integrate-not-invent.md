# 0001 — Integrate, don't reinvent — and guard conformance

**Status:** accepted · **Scope:** P04 (service & security), P05 (agent gateway), P07 (observability)

## Context

An adversarial review of the P04/P05/P07 code found roughly ~880 LOC that re-implemented
things best-of-breed libraries already do. AgentShip's whole thesis is *integrate best-of-breed
libraries behind small, stable seams — never reinvent them*. Two follow-on constraints shaped the
fix: we must stay **provider-switchable** (keep the seam where a real second provider is on the
roadmap), and we favour **readability** over taking a heavy dependency that fits a surface poorly.

The rule we applied: **keep the thin swap-seam; behind it, delegate to the OSS library — never
re-implement it.** Where a genuine reason forces us to keep our own thin thing (a vendor-free
contract, a heavy/ill-fitting SDK), we do not just trust it — we add a **conformance guard**: a
test that validates our output against the upstream standard so we cannot silently drift.

## Decisions

### Delegated to the real library (seam kept, guts replaced)

| Area | Was | Now |
|---|---|---|
| **JWKS validation** (`auth/jwt.py`) | hand-rolled `JwksCache` (fetch + kid→key map + TTL + rotation, ~63 LOC) | PyJWT's `PyJWKClient`, run via `asyncio.to_thread`; seam still validates static-key **or** JWKS and maps claims to `Caller` |
| **SSE framing** (`/v1 :stream`, A2A `message/stream`) | hand-built `event:`/`data:` strings over `StreamingResponse` (no keepalive, no disconnect handling) | `sse-starlette`'s `EventSourceResponse` — owns the wire encoding, keepalive comments, and client-disconnect cancellation |

### Kept our thin thing — with a conformance guard

Sometimes adopting the library is the wrong call. In each case below we keep a small vendor-free
implementation **and** prove it conforms to the upstream standard.

| Area | Why we kept ours | Guard |
|---|---|---|
| **Semconv keys** (`observability/semconv.py`) | The kernel is vendor-free by contract — core cannot import `opentelemetry.semconv`, and engine hooks import these keys with observability *not* installed. | `test_semconv_upstream.py` asserts every `gen_ai.*`/`llm.*` string equals OTel-GenAI / OpenInference upstream. |
| **A2A wire models** (`a2a/models.py`) | `a2a-sdk` 1.x is protobuf-first; its only Pydantic/JSON layer is the legacy `compat.v0_3` shim. Adopting either forces grpc/protobuf lock-in and protobuf-JSON semantics into a clean Pydantic/FastAPI JSON service. | `test_a2a_conformance.py` (extra `agentship-service[a2a]`) validates every AgentCard / Message / status frame against `a2a-sdk`'s own schema. |
| **RecordingObserver** (`observability/recorder.py`) | Core is vendor-free and the base install ships without OTel; offline verifiers read our own `SpanNode`/`TraceView` tree, not OTel's flat `ReadableSpan`. It is the in-memory impl of our own Observer port, not OTel's `InMemorySpanExporter`. | `test_observer_parity.py` runs one interaction through both this recorder and the real OTel observer and asserts the trees agree. |

The A2A guard immediately earned its keep — it caught four real spec violations, now fixed:
`Message.messageId` and `TaskStatusUpdateEvent.contextId` are required (we emitted neither); the
advertised `apiKey` security scheme must declare `in`/`name`; the `oauth2` scheme must carry an
`OAuthFlows` object; and the `mtls` scheme's A2A `type` is spelled `mutualTLS`. The card now emits
each scheme in its conformant shape — `oauth2` advertises the client-credentials flow (token URL +
scopes) when the author declares one, or spec-valid empty flows otherwise.

### Deliberately *not* changed

- **Phoenix exporter** stays on the standard **OTLP/HTTP** exporter, not `arize-phoenix-otel`'s
  `register()`. `register()` installs a *global* tracer provider, which fights our factory that
  composes processors into its own provider. Plain OTLP already reaches Phoenix vendor-neutrally
  with zero SDK lock-in — that *is* the thesis.

## Consequence

The seams that make AgentShip provider-switchable (Engine, the vendor-free Observer boundary, Auth)
are unchanged; their adapters got thinner by delegating to real SDKs, and the three vendor-free
pieces we must keep are now continuously proven against their upstream standards. Net: less code we
maintain, and the code we do keep can't drift from the specs it claims to speak.
