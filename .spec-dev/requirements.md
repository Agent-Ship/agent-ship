# Requirements: AgentShip

## Problem
Standing up a real agentic system means gluing together ~10 libraries by hand — an
orchestration engine, a model gateway, tools/MCP, memory, tracing, evals, a sandbox,
a serving layer, a deploy target — and re-solving the seams (identity, sessions, cost,
handoffs) every time. There is no open, recipe-first harness that wires best-of-breed
tools behind uniform seams and lets you swap any one of them.

## Goal
Author an agent or a **team of agents** in YAML (or Python) and get the plumbing wired
by **integrating** best-of-breed libraries behind small, stable seams — never
reinventing them. Run it, serve it, deploy it, observe it, evaluate it.

## Capability pillars (ALL in scope — delivered incrementally, one working slice each)
1. **Authoring** — YAML + Python (`code:`) authoring of a single agent or a team.
2. **Models** — any provider via LiteLLM; per-agent/per-member models; resilience.
3. **Tools** — Python functions + MCP servers that actually execute.
4. **Structured output** — schema-enforced results.
5. **Streaming** — token streaming with clean terminal/error events and cost.
6. **Multi-agent + structured handoffs** — real coordinator/handoff/swarm topologies
   (via LangGraph's prebuilt multi-agent), not a hand-rolled router.
7. **Observability** — nested traces (agent → member → llm/tool), tokens, cost;
   OpenTelemetry + Langfuse/Opik.
8. **Memory** — cross-session long-term memory (mem0), correctly scoped.
9. **Durable execution** — checkpoints + resume; `session_id == thread_id`.
10. **Serving + interop** — REST/SSE, session-aware; expose an agent as an MCP server.
11. **Evals** — score agent/team outputs.
12. **Sandbox** — run untrusted tool/code safely.
13. **Deploy** — Docker → kagent / behind agentgateway.
14. **A2A** — agent-to-agent protocol.
15. **Recipes** — a growing catalog of ready-to-run harness recipes.

## Non-negotiables (how we build)
- **Incremental.** One thin vertical slice per phase; each works end-to-end on its own.
- **Test-first (TDD).** A failing test defines "done" before the code exists.
- **Everything works.** No stubs marked done. Live proofs run in CI (recorded cassettes).
- **Demo updated every slice.** `examples/` shows the new capability, verified by a test.
- **Tasks tracked live** in the phase file, `tasks.md`, and Notion the moment CI is green.
- **CI is the gate.** Nothing is done until CI is green on the pushed branch.

## Non-goals (v1)
- Hosted multi-tenant SaaS.
- Reinventing any best-of-breed library (orchestration, gateway, tracing, memory…).
- Building later pillars ahead of the ones they depend on.

## Success criteria
At every phase boundary: `agentship run`/`serve` works for the capabilities shipped so
far, the demo runs, the suite (incl. replayed live proofs) is green in CI, and the three
trackers agree with the code.
