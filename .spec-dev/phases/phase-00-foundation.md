# Phase 0 — Foundation (walking skeleton)  🔧 IN PROGRESS

Goal: the smallest thing that runs end-to-end on a clean, scalable kernel, with the
operating model (TDD + CI + tracking) in place from the first commit. No vendor code yet —
a trivial `echo` engine proves the whole spine (author → build → run/stream → CLI) works.

## Deliverable (what must work)
```bash
agentship run examples/hello.yaml --input "hi"   # prints: echo: hi
pytest -q                                          # green
```
CI runs the suite on push/PR; pre-commit runs ruff + changed-area tests.

## Tasks
- [ ] **Package skeleton** — single package `agentship` (`pyproject.toml`, extras
      `[mcp,memory,observability,all]`, ruff config, `py.typed`); dev venv; `make test`.
- [ ] **Kernel: spec** — `AgentSpec` + `MemberSpec` (pydantic, `extra="forbid"`), YAML
      loader (`safe_load`), `code:` Python-authoring hook. Test: load + reject unknown key.
- [ ] **Kernel: context** — `RunContext` (user_id, session_id, run_id, agent_name;
      `memory_scope` = (user_id, agent_name)); `current_run` contextvar. Test: isolation
      across concurrent runs; caller session_id honored; run_id unique per turn.
- [ ] **Kernel: engine contract** — `Engine` base + `EngineCapabilities`; `Registry[T]`
      with entry-point discovery; capability fail-fast at build. Test: unsupported spec
      raises a clear `CapabilityError`.
- [ ] **Echo engine** — trivial engine implementing run + stream. Test: run returns
      `echo: <input>`; stream yields ≥1 chunk then a terminal event.
- [ ] **Runtime** — `build_agent(spec)` + `RunnableAgent.run/stream` with the middleware
      runner (on_request/on_response/on_error) and the capability gate. Test: middleware
      order (onion); on_error fires and re-raises; contextvar reset on all paths.
- [ ] **CLI** — `agentship run <file> --input ...` and `--stream`. Test: CLI runs the echo
      agent and prints output.
- [ ] **Demo** — `examples/hello.yaml` + a README snippet; a test that runs it.
- [ ] **CI + pre-commit** — `.github/workflows/ci.yml` (offline suite on push/PR),
      `.pre-commit-config.yaml` (ruff + changed-area tests + files-per-commit cap).

## Proof
`pytest -q` green; `agentship run examples/hello.yaml --input hi` → `echo: hi`; CI green
on the pushed branch; every task above committed separately with its proving test named.

## Notes
This kernel is deliberately vendor-free so Phase 1+ adapters (LangGraph, LiteLLM, MCP,
mem0, OTel) attach to a stable base. The `echo` engine stays forever as the zero-dependency
conformance target every real engine is tested against.
