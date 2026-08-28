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

### Testing & verification tooling (2026-08-25)
- Promoted the conformance capability catalogue into the shipped, vendor-free `agentship.conformance`
  module (importable from `agentship-core`, no longer test-only): `CAPABILITIES`, `Capability`,
  `run_capability_grid`, `CellResult`, and `DEFERRED_CAPABILITIES`. Imports only `agentship.*` /
  `pydantic` / stdlib — never langchain/langgraph/litellm.
- Moved the LangGraph offline harness into `agentship_langgraph.testing` (the `offline` model-seam
  provider + `build_hitl_agent` factory), passed *into* the grid so the catalogue stays engine-neutral.
- Added the `hitl` conformance cell; `langgraph` now declares `hitl="interrupt"` and proves it by
  surfacing a real interrupt pause rather than running straight through.
- Added the `agentship verify [--agents-dir DIR] [--live/--offline]` CLI — the user-facing "verifiable
  agents" honesty report over the same grid, plus spec-validation, observability, service-contract, and
  A2A-interop sections that report SKIPPED (with a reason) when their optional dependency or target is
  absent. Runs fully offline; no provider keys.
- Fixed stale phase numbers in the conformance surface.
- Offline baseline: **687 passed / 14 skipped / 2 xfailed**.

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

### P02 — Multi-agent supervisors (in-flight, 18/19 reconciled)
- Supervisor orchestration over plain LangGraph `StateGraph`:
  `classify → lookup_route → dispatch → resolve → confirm_write? → safety_gate`. Routing is a pure
  lookup, not an LLM handoff, so the same input always takes the same path — the property P11's
  identical-resume guarantee depends on. `langgraph-supervisor` deliberately not adopted:
  [ADR 0002](decisions/0002-plain-stategraph-supervisor.md).
- Three member shapes the supervisor treats identically: **inline**, **`ref:`** (another agent's
  YAML), and **`a2a:`** (an agent on another host). Local → networked is a one-line change.
- Fully declarative supervisors — `members:` with `ref:` and `description:`, **zero Python**.
  A `code:` factory remains available when custom routing is wanted.
- `ConflictResolver` merges competing specialist answers by priority list with an explicit
  tie-break (`first_by_priority` / `highest_confidence`); pure — no model call, no I/O.
- Capability page: [multi-agent.md](capabilities/multi-agent.md).
- Open: `classify` does not yet stamp `TaskHint(model=…)`, so an easy branch cannot be routed to a
  cheaper model.

> **Durability moved out of P02.** Checkpointing/HITL is now P03 and crash-resume is P11 — see the
> renumber note at the bottom of this file.

### P01 — Engine & LangGraph agent (delivered)
- `LangGraphAgent` behind the `Engine.build` seam (`build_agent(spec)` → `RunnableAgent`) with `run` / `stream` / `resume`.

### P00 — Foundation & contracts (delivered 2026-08-07, panel-scored 9.7/10)
- Vendor-free kernel: core base classes (`RunContext`, `Tool`, the `Engine` ABC), the agent/spec model,
  and the registry. Everything above the kernel is a thin adapter behind these seams.

---

## Note on phase numbers (renumbered 2026-08-23)

Phases were renumbered so that **one number = one feature**. Entries above are being migrated to the
new numbering as each phase's docs are finished; a heading not yet migrated still carries its old
number. The mapping for the headings in this file:

| Old | Was | New |
|---|---|---|
| P02 | Multi-agent & durability | **02** Multi-Agent Supervisors · **03** Checkpointing & HITL · **11** Durable Resume |
| P03 | Tools & MCP | **04** Tools & MCP |
| P04 | Service & security | **06** Service Endpoints · **07** Auth · **08** Tenant Isolation · **09** HTTP Posture & Deploy |
| P05 | Agent gateway | **18** A2A Core · **19** A2A Push |
| P07 | Observability | **05** Observability |

Full crosswalk and the authoritative board: [`.spec-dev/STATUS.md`](../../.spec-dev/STATUS.md).
