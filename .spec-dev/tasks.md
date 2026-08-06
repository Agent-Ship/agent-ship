# Tasks — live tracker

Status: `[ ]` todo · `[~]` in progress · `[x]` done (with commit SHA + proof).
Mirrors the phase ladder in `architecture.md`. Detailed per-phase tasks live in
`phases/phase-NN-*.md`. Notion tracker mirrors this (same SHAs).

| Phase | Status | Notes |
|---|---|---|
| 0 Foundation | [x] | walking skeleton green (4834a87); `agentship run` → `echo: hi`; 30 tests, ruff clean. CI workflow committed (runs on push). |
| 1 Real model | [x] | LangGraph+LiteLLM single agent; real gpt-4o-mini via replayed cassette; clean run errors + `.env` loading (G3/G4); 53 tests keyless, ruff clean (ab46a2f) |
| 2 Identity backbone | [ ] | |
| 3 Tools | [ ] | |
| 4 Structured output | [ ] | |
| 5 Streaming | [ ] | |
| 6 Multi-agent + handoffs | [ ] | |
| 7 Observability | [ ] | |
| 8 Memory | [ ] | |
| 9 Durable | [ ] | |
| 10 Serve + interop | [ ] | |
| 11 Evals | [ ] | |
| 12 Sandbox | [ ] | |
| 13 Deploy | [ ] | |
| 14 A2A | [ ] | |
| 15 Recipes | [ ] | |
