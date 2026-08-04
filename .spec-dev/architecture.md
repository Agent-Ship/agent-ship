# Architecture & phase ladder: AgentShip

Status: Draft (foundation). Last updated: 2026-08-04.

## Design principles (learned the hard way)
1. **Small kernel, thin adapters.** The kernel knows nothing about any vendor. Each
   integration is a thin adapter behind a small seam. If an adapter is more than a
   wrapper, we're reinventing — stop.
2. **Integrate, never reinvent.** Orchestration = LangGraph (incl. its *prebuilt*
   multi-agent). Models = LiteLLM. Tools = MCP + LangChain tool objects. Memory = mem0.
   Tracing = OpenTelemetry + Langfuse/Opik. We wire, we don't rebuild.
3. **One extension mechanism, used only where earned.** A single `Registry` for engines
   and adapters, populated by entry points. We add a seam when a *second* real
   implementation (or a concrete near-term one) exists — never speculatively.
4. **Honest capabilities.** An engine declares what it supports; the kernel fails fast
   at build time if a spec asks for something the engine can't do. No silent drops.
5. **Identity backbone from day one.** One `RunContext` carries `user_id`, `session_id`,
   `run_id`, `agent_name`. Invariant: `session_id == engine thread_id == checkpoint key`.
   Memory scope, trace ids, and durable resume all read this one object.
6. **Every capability is provable.** Offline test asserts the mechanism (not a constant);
   the live path is recorded as a cassette and replayed in CI.

## Packaging
A **single package `agentship`** with optional-dependency extras, not a fleet of dists:
`pip install agentship[mcp,memory,observability]` (and `[all]`). Extras keep the core
dependency-light (pydantic + pyyaml) while integrations stay optional. We split into a
second distribution only if a real need appears — not before.

```
src/agentship/
  spec.py            AgentSpec + MemberSpec + YAML/Python loading
  context.py         RunContext (the identity backbone)
  runtime.py         run()/stream(), the middleware runner, capability gate
  registry.py        Registry[T] + entry-point discovery (engines, adapters)
  errors.py          the error taxonomy
  engines/
    base.py          Engine contract + EngineCapabilities
    echo.py          trivial engine (walking skeleton, no deps)
    langgraph/       default engine + templates (single, supervisor, swarm)  [phase 1+]
  models/            LiteLLM model seam                                        [phase 1]
  tools/             Python + MCP tool providers                              [phase 3]
  observability/     OpenTelemetry + Langfuse/Opik adapters                   [phase 7]
  memory/            mem0 adapter + memory middleware                         [phase 8]
  service/           FastAPI REST/SSE                                         [phase 10]
  cli.py             the `agentship` CLI
```
Seams for later pillars (evals, sandbox, deploy, a2a, recipes) attach to the same
registry + `RunContext` without kernel edits — that is what "scalable foundation" means.

## Phase ladder (dependency-ordered; each phase = one working, demoable, tested slice)
Detail is written just-in-time in `phases/phase-NN-*.md` when a phase starts; only the
next few are specified. Everything from `requirements.md` is here — nothing is cut.

| # | Phase | Ships (user-visible) | Integrates | Depends on |
|---|---|---|---|---|
| 0 | **Foundation** | `agentship run echo.yaml` prints output; CI green; TDD + tracking in place | — (kernel only) | — |
| 1 | Real model | `agentship run` a single YAML agent → real LLM answer | LiteLLM + LangGraph | 0 |
| 2 | Identity backbone | stable `session_id`/`run_id`; same session across turns | (kernel) | 1 |
| 3 | Tools | agent calls a Python fn + an MCP server and uses the result | MCP SDK | 2 |
| 4 | Structured output | `output:` schema → validated object | LangGraph `response_format` | 1 |
| 5 | Streaming | token SSE + terminal `done`/`error` + recorded cost | LangGraph `astream` | 2 |
| 6 | **Multi-agent + handoffs** | a team with real structured handoffs | LangGraph prebuilt supervisor/swarm | 3 |
| 7 | Observability | nested trace (agent→member→llm/tool) + tokens/cost in Langfuse | OTel + Langfuse/Opik | 2,3,6 |
| 8 | Memory | cross-session recall, scoped per user+agent | mem0 | 2 |
| 9 | Durable | kill → `--resume`; checkpoints | LangGraph checkpointers (+Postgres) | 2 |
| 10 | Serve + interop | REST/SSE session-aware; agent-as-MCP-server | FastAPI, MCP SDK | 2,6 |
| 11 | Evals | score team outputs | (eval lib TBD) | 6,7 |
| 12 | Sandbox | run untrusted tools safely | (E2B/gVisor TBD) | 3 |
| 13 | Deploy | Docker → kagent / agentgateway | kagent, agentgateway | 10 |
| 14 | A2A | agent-to-agent protocol | A2A SDK | 10 |
| 15 | Recipes | browsable catalog of ready recipes | — | 6,7,8,10 |

## Open decisions (resolved just-in-time, not up front)
Eval lib (11), sandbox provider (12), Postgres topology (9), deploy boundary who-owns-
authz (13). We decide each when its phase starts, not now — deciding early is how the
last attempt over-built.
