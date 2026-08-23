# Changelog

All notable, user-facing changes to AgentShip, grouped by delivery phase. This file is a
**Definition-of-Done gate**: a phase is not "done" until its capability page, this entry, and an ADR
(when a non-obvious decision was made) all land. See `.spec-dev/operating-model.md` (DOCS gate) and
`.spec-dev/STATUS.md` (the authoritative board).

The format is loosely based on [Keep a Changelog](https://keepachangelog.com). This project has not
yet cut a tagged release; entries are grouped by phase until v0.1 ships (P00–P07).

<!-- BACKFILLED 2026-08-23: entries for the seven already-shipped phases (P00,P01,P02,P03,P04,P05,P07)
     were reconstructed from code+tests during the status-reconciliation pass, not written at ship
     time. From the P08+ phases onward, entries land WITH the phase as a DoD gate. -->

## [Unreleased]

### P07 — Observability (delivered 2026-08-18, `feat/phase-07-observability`)
- OpenTelemetry-based tracing behind a vendor-free `Observer` seam; the kernel never imports OTel.
- Full trace tree: AGENT root span with model / tool / MCP / graph-node child spans (in-flight — see
  STATUS.md for the remaining real-MCP-server full-trace-tree test).
- OTLP/HTTP export (Phoenix-compatible) with no vendor SDK lock-in; `RecordingObserver` for offline
  trace assertions, proven at parity with the real OTel path (`test_observer_parity.py`).
- Vendor-free semantic-convention keys guarded against OTel-GenAI / OpenInference upstream
  (`test_semconv_upstream.py`).

### P05 — Agent gateway / A2A (in-flight, 15/24 reconciled)
- Agent-to-Agent (A2A) wire models and AgentCard emission, conformance-guarded against `a2a-sdk`
  (`test_a2a_conformance.py`). Core A2A path done (39 tests). Task-bridge + push blocked on P11's
  `on_state_change` hook — see STATUS.md.

### P04 — Service & security (in-flight, 40/44 reconciled)
- FastAPI service exposing agents over REST + SSE streaming (`sse-starlette`) + WebSocket.
- `AuthProvider` seam with ForwardedHeader / ApiKey / JWT (PyJWT `PyJWKClient`) adapters; scope-based
  `authorize()`. Per-tenant isolation with `TenantViolation` on cross-tenant access.
- HTTP security posture: CORS, security headers, optional rate-limit (off by default, no Redis
  required). Open: `agentship deploy` CLI, Postman collection verify, dev-ingress lockdown cell.

### P03 — Tools & MCP (in-flight, 26/28 reconciled)
- Native tool calling on agents; MCP integration via `langchain-mcp-adapters`
  (`MultiServerMCPClient`) for local + remote servers — not hand-rolled.
- deepagents-style autonomous tool use. Open: graceful tool-error handling test; combined
  native-skill + MCP-tool demo.

### P02 — Multi-agent & durability (in-flight, 40/42 reconciled)
- Supervisor / multi-agent orchestration over plain LangGraph `StateGraph` (deterministic routing;
  langgraph-supervisor deliberately not adopted). `ConflictResolver` for concurrent state writes.
- Durability via LangGraph checkpointing (`durability=checkpoint`); `ResumeToken` + `engine.resume`
  seam; idempotency ledger (`call_once` / `idem_key`); HITL `interrupt` seam.
- Open (real remaining risk): no integrated test yet of supervisor + checkpoint + mid-run failure +
  resume; idempotency-across-a-supervisor-dispatch unproven. Auto-resume trigger lives in P11.

### P01 — Engine & LangGraph agent (delivered)
- `LangGraphAgent` / `BaseAgent.build` engine seam with `run` / `stream` / `resume`.

### P00 — Foundation & contracts (delivered 2026-08-07, panel-scored 9.7/10)
- Vendor-free kernel: core ports (`RunContext`, `Tool`, `MiddlewareEngine`), the agent/spec model,
  and the registry. Everything above the kernel is a thin adapter behind these seams.
