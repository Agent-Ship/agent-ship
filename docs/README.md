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

| Page | Phase | Status |
|---|---|---|
| [Foundation & contracts](capabilities/foundation-contracts.md) | P00 | ✅ delivered |
| [Engine & agent](capabilities/engine-and-agent.md) | P01 | ✅ delivered |
| [Multi-agent & durability](capabilities/multi-agent-and-durability.md) | P02 | 🟨 40/42 |
| [Tools & MCP](capabilities/tools-and-mcp.md) | P03 | 🟨 26/28 |
| [Service & security](capabilities/service-and-security.md) | P04 | 🟨 40/44 |
| [Agent gateway / A2A](capabilities/agent-gateway-a2a.md) | P05 | 🟨 15/24 |
| [Observability](capabilities/observability.md) | P07 | ✅ delivered |

Counts mirror `.spec-dev/STATUS.md` at the time of the 2026-08-23 backfill; that board is the source
of truth if they ever disagree.
