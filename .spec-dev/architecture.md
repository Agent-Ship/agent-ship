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

## Packaging (multi-package monorepo)
A **monorepo of pip packages** under `packages/*/`, each an installable dist, wired
together by entry points — add a vendor or a pillar = a new package + one entry-point
line, **no core edits**. `pip install agentship` (the meta-package) gives the kernel +
default engine + CLI out of the box; extras add the rest. (This reverses the earlier
single-package draft; multi-package is the locked decision — it matches the plugin /
swappable-seams thesis and lets third parties publish `agentship-<engine>` on their own.)

**Phase-1 set (4 dists; one new package per pillar phase after that):**

| Package (dist) | Import name | Holds | Depends on |
|---|---|---|---|
| **agentship-core** | `agentship` | kernel: spec, context, runtime, registry, errors, middleware, `engines/{base,echo}` | `pydantic`, `pyyaml` |
| **agentship-langgraph** | `agentship_langgraph` | LangGraph engine + LiteLLM model source (registers `langgraph` engine + `litellm` source) | `agentship-core`, `langgraph`, `langchain-litellm` |
| **agentship-cli** | `agentship_cli` | the `agentship` command; owns the console script | `agentship-core`, `click`, `python-dotenv` |
| **agentship** (meta) | — | easy install: depends on core+langgraph+cli; extras `[mcp]`,`[memory]`,`[observability]`,`[service]`,`[all]` pull sibling dists | the set |

```
pip install agentship          # kernel + langgraph engine + CLI → a real agent works
pip install "agentship[all]"   # every pillar
pip install agentship-core     # kernel only (bring your own engine)
```
Rules: core stays `pydantic`+`pyyaml` forever; each pillar phase adds **one package + one
entry-point group** (`agentship.engines` / `.model_sources` / `.tool_providers` /
`.observers` / `.memory_backends` / …); an engine/adapter that isn't installed yields a
clean "install `agentship-<x>`" error, never an opaque registry miss.

## Phase ladder (dependency-ordered; each phase = one working, demoable, tested slice)
Detail is written just-in-time in `phases/phase-NN-*.md` when a phase starts (using the
phase-spec template in `operating-model.md`). Each row's "Ships" is the **new assertion**
the phase must prove — not a capability name that's already partly built. The **Pkg**
column is the package the phase creates/changes. Everything in `requirements.md` is
scheduled here or in the Backlog below (see the honesty note).

| # | Phase | Ships (the new proof) | Pkg | Depends on |
|---|---|---|---|---|
| 0 | **Foundation** | `agentship run echo.yaml` prints output; TDD + tracking in place | core | — |
| 1 | Real model | `agentship run` a single YAML agent → real LLM answer (cassette-proven) | langgraph | 0 |
| 1b | Model tuning + local | `params:` (temperature/max_tokens) + `api_base` → Ollama/vLLM/self-hosted | langgraph | 1 |
| 2 | Sessions (multi-turn) | caller `session_id` threaded as LangGraph `thread_id`; same session remembers across turns | core+langgraph | 1 |
| 3 | Tools | agent calls a Python fn + an MCP server and **uses** the result | mcp | 2 |
| 4 | Structured I/O | `output:` schema → validated object; typed `inputs:` validated at the boundary | langgraph | 1 |
| 5 | Streaming polish | streamed run records cost + emits terminal `error`/`done` (delta over P1's chunking) | langgraph | 2 |
| 6 | **Multi-agent + handoffs** | a team with real structured handoffs + **per-member models** | langgraph | 3 |
| 7 | Observability | nested trace (agent→member→llm/tool) + tokens/cost in Langfuse/Opik; feedback score API | observability | 2,3,6 |
| 8 | Memory | cross-session recall, scoped per user+agent | memory | 2 |
| 9 | Durable + session store | kill → `--resume`; checkpoints; Postgres session store | langgraph+service | 2 |
| 10 | Serve + interop | REST/SSE session-aware; WebSocket; agent-as-MCP-server; MCP OAuth | service+mcp | 2,6 |
| 11 | Evals | score team outputs | evals | 6,7 |
| 12 | Sandbox + guardrails | run untrusted tools safely; PII guardrails (Presidio) | sandbox | 3 |
| 13 | Deploy | Docker → kagent / agentgateway | deploy | 10 |
| 14 | A2A + Skills | agent-to-agent protocol; reusable skills (tools+prompt+middleware) | a2a | 10 |
| 15 | Recipes + Studio | browsable recipe catalog; file/PDF recipe; debug UI | recipes | 6,7,8,10 |

## Parity backlog — everything the old repo had is scheduled (not cut)
The old `agent-ship` had features the first draft of this plan omitted. To keep "nothing
is silently dropped" TRUE, each is scheduled above: **per-member models** → P6;
**params/`api_base` for local models** → P1b; **sessions/Postgres store** → P2/P9;
**WebSocket + MCP OAuth** → P10; **feedback API** → P7; **input validation** → P4;
**guardrails (Presidio)** → P12; **Skills** → P14; **file/PDF recipe + Studio** → P15;
**Opik nested traces** → P7; **deploy (Docker/kagent)** → P13. The old parity matrix is
imported at `parity.md` and each row points at its phase. Anything genuinely out of scope
is an explicit Non-goal in `requirements.md` — not an unlisted omission.

## Honesty note (what Phase 0/1 already shipped)
`RunContext` (identity backbone), the capability gate, and token-by-token streaming were
already built in Phases 0–1. So later phases must prove a **new** assertion, not re-claim
an existing capability: P2 = multi-turn memory of a session, P5 = cost+error-event in the
stream. Phase specs state the delta explicitly.

## Open decisions (resolved just-in-time, not up front)
Eval lib (11), sandbox provider (12), Postgres topology (9), deploy authz owner (13),
Skills shape (14). Decided when the phase starts — deciding early is how the last attempt
over-built.
