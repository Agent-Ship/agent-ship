# Tasks — live tracker

Status: `[ ]` todo · `[~]` in progress · `[x]` done (with commit SHA + proof).
Mirrors the phase ladder in `architecture.md`. Detailed per-phase tasks live in
`phases/phase-NN-*.md`. Notion tracker mirrors this (same SHAs).

| Phase | Status | Notes |
|---|---|---|
| 0 Foundation | [x] | walking skeleton green (4834a87); `agentship run` → `echo: hi`; 30 tests, ruff clean. CI workflow committed (runs on push). |
| 1 Real model | [x] | LangGraph+LiteLLM single agent; clean errors + `.env` (G3/G4); provider-matrix infra + examples (G5, ae8ecbe); 59 pass/4 skip keyless, ruff clean. ⚠️ live proof still OpenAI-only — Claude/Gemini recording BLOCKED on working keys |
| **Packaging split** | [x] | monorepo of 4 dists (core + langgraph + cli + agentship meta); 59 pass/4 skip keyless, history preserved (a468b08) |
| **Demo repo** | [~] | formalize `agentship-demo/`: git init, pin, .env.example, rewrite to single agent, cassette smoke test |
| 1b Model tuning + local | [ ] | `params:` (temperature/max_tokens) + `api_base` → Ollama/vLLM/self-hosted |
| 2 Sessions (multi-turn) | [ ] | caller session_id → LangGraph thread_id; remembers across turns |
| 3 Tools | [ ] | |
| 4 Structured I/O | [ ] | |
| 5 Streaming polish | [ ] | cost + terminal error/done (delta over P1 chunking) |
| 6 Multi-agent + handoffs | [ ] | incl. per-member models |
| 7 Observability | [ ] | |
| 8 Memory | [ ] | |
| 9 Durable | [ ] | |
| 10 Serve + interop | [ ] | |
| 11 Evals | [ ] | |
| 12 Sandbox | [ ] | |
| 13 Deploy | [ ] | |
| 14 A2A | [ ] | |
| 15 Recipes | [ ] | |
