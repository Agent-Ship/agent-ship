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
- **Multi-package pip layout.** A monorepo of installable packages (`agentship-core` +
  per-pillar adapters + an `agentship` meta) so install is easy and adapters are pluggable.
- **Test-first (TDD).** A failing test defines "done" before the code exists.
- **Everything works.** No stubs marked done. Every real-provider/network path is proven by
  a recorded, replayed cassette (offline mechanism tests too). "Done" is verified, not asserted.
- **Demo updated every slice.** The framework's `examples/` shows each capability in
  isolation (cassette-tested); the separate **demo repo** (`agentship-demo/`) is a realistic
  app that installs AgentShip via pinned pip and has its own smoke test so it can't rot.
- **Commits.** One task = one commit; no `Co-Authored-By`/"Generated with" trailers.
- **Tasks tracked live** in the phase file, `tasks.md`, and Notion the moment it lands.
- **CI is the gate — when enabled.** CI is currently OFF by owner's call; until then, "done"
  = full suite green locally + demo smoke green. When the remote/gate is turned on, green CI
  on the branch becomes the hard gate.

## Nothing is cut — everything is scheduled
Every capability the old `agent-ship` had is scheduled into a phase (see the ladder + the
**Parity backlog** in `architecture.md`, and the imported matrix in `parity.md`). If a
feature is genuinely out of scope it is listed as a Non-goal below — never silently dropped.

## Non-goals (v1)
- Hosted multi-tenant SaaS.
- Reinventing any best-of-breed library (orchestration, gateway, tracing, memory…).
- Building later pillars ahead of the ones they depend on.

## Success criteria
At every phase boundary: `agentship run`/`serve` works for the capabilities shipped so far,
the framework examples + the demo repo run (offline/cassette), the suite is green (locally
now, in CI once enabled), and all three trackers agree with the code.
