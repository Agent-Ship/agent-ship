# Parity matrix — old `agent-ship` → new `agentship`

Every capability the old repo had, its status in the new rebuild, and the phase that
delivers it. This is the receipt behind "nothing is cut": every row is either **scheduled**
or an explicit **Non-goal**. (Old repo = reference, not source of truth; it was
over-engineered and some rows were stubs even there.)

| Capability | Old repo status | New status | Scheduled |
|---|---|---|---|
| YAML/`code:` authoring | real | **done** | P0/P1 |
| LangGraph engine + LiteLLM (any provider) | real | **done** (OpenAI live; Claude/Gemini blocked on keys) | P1 |
| Per-agent model | real | **done** | P1 |
| `params` (temperature/max_tokens) + `api_base` (local: Ollama/vLLM) | real | todo | **P1b** |
| RunContext identity backbone | real | **done** | P0 |
| Capability fail-fast | real | **done** | P0 |
| Multi-turn sessions (session_id==thread_id) | partial | todo | **P2** |
| Python + MCP tools (execute) | real | todo | **P3** |
| Agent-as-MCP-server | real | todo | **P10** |
| MCP OAuth + token store | real (on old `main`) | todo | **P10** |
| Auto-Tool-Docs | real | todo | **P3** (opt-in) |
| Structured output (`output:`) | real | todo | **P4** |
| Typed `inputs:` validation | real | todo | **P4** |
| Streaming (chunks) | real | **done** | P1 |
| Streaming cost + terminal error/done | real | todo | **P5** |
| Multi-agent supervisor | real | todo | **P6** |
| Per-member models | real | todo | **P6** |
| Observability: nested traces + tokens/cost | real (Opik depth) | todo | **P7** |
| Cardinality-limited metrics | real | todo | **P7** |
| Langfuse + Opik adapters | real | todo | **P7** |
| Feedback (thumbs) API | real | todo | **P7** |
| Long-term memory (mem0), scoped | real | todo | **P8** |
| pgvector memory option | spec-only | todo | **P8** (option) |
| Durable execution / checkpointers | stub | todo | **P9** |
| Postgres session store | stub | todo | **P9** |
| REST/SSE serving + auth + error taxonomy | real | todo | **P10** |
| WebSocket serving | dropped | todo | **P10** |
| Evals | not built | todo | **P11** |
| Sandbox | not built | todo | **P12** |
| Guardrails (Presidio) | spec field only | todo | **P12** |
| Deploy (Docker → kagent/agentgateway) | stub | todo | **P13** |
| A2A protocol | not built | todo | **P14** |
| Skills (built-in + custom) | real | todo | **P14** |
| Recipe catalog (browsable) | scaffold only | todo | **P15** |
| File/PDF analysis recipe | pattern | todo | **P15** |
| AgentShip Studio (debug UI) | real | todo | **P15** |
| `describe()` → MCP/A2A/OpenAPI projection | real | todo (seam as pillars land) | P3/P10/P14 |
| Offline-first tests + cross-engine conformance | real | **done** (kept, fakes must depend on tested behavior) | ongoing |

**Non-goals (explicitly not v1):** hosted multi-tenant SaaS; reinventing any best-of-breed
library. Everything else above has a phase.
