# AgentShip docs

User-facing documentation for the AgentShip framework. Kept in sync with the code by a
**Definition-of-Done gate** (`.spec-dev/operating-model.md` → DOCS): a phase is not done until its
capability page, a `CHANGELOG.md` entry, and an ADR (when a non-obvious decision was made) all land.
The authoritative delivery board is `.spec-dev/STATUS.md`.

## Layout

- [`CHANGELOG.md`](CHANGELOG.md) — user-facing changes, grouped by delivery phase.
- [`capabilities/`](capabilities/) — one page per shipped capability: what it does, how to use it, a
  runnable example, and its honest status.
- [`decisions/`](decisions/) — Architecture Decision Records. Start from
  [`_TEMPLATE.md`](decisions/_TEMPLATE.md) for a new one.

## Capabilities

One page per capability — and, since the 2026-08-23 renumber, **one page per phase**. Phase numbers
below are the current ones.

| Page | Phase | Status |
|---|---|---|
| [Foundation & base classes](capabilities/foundation.md) | 00 | ✅ delivered |
| [Engine & agent](capabilities/engine-and-agent.md) | 01 | ✅ delivered |
| [Multi-agent supervisors](capabilities/multi-agent.md) | 02 | 🟨 18/19 |
| [Checkpointing & HITL](capabilities/checkpointing-and-hitl.md) | 03 | 🟨 8/9 |
| [Tools & MCP](capabilities/tools-and-mcp.md) | 04 | 🟨 26/28 |
| [Observability](capabilities/observability.md) | 05 | ✅ delivered |
| [Service & security](capabilities/service-and-security.md) | 06–09 | 🟨 40/44 aggregate |
| [Durable resume](capabilities/durable-resume.md) | 11 | 🟨 11/13 — headline proof missing |
| [Agent gateway / A2A](capabilities/agent-gateway-a2a.md) | 18–19 | 🟨 15/24 |
| [Verify & conformance](capabilities/verify-and-conformance.md) | cross-cutting | ✅ |

`.spec-dev/STATUS.md` is the source of truth if these ever disagree.

## Decisions

| ADR | Decision |
|---|---|
| [0001](decisions/0001-integrate-not-invent.md) | Integrate, don't reinvent — and guard conformance |
| [0002](decisions/0002-plain-stategraph-supervisor.md) | Build the supervisor on plain `StateGraph`, not `langgraph-supervisor` |
| [0003](decisions/0003-langgraph-checkpointer-as-the-durability-substrate.md) | LangGraph's checkpointer is the durability substrate; `durability:` stays vendor-free |
