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
- [x] **Package skeleton** — single package `agentship` (`pyproject.toml`, extras
      `[mcp,memory,observability,all]`, ruff config, `py.typed`); dev venv; `make test`.
      (e5c6212, proof: tests/test_cli.py::test_cli_run_prints_echo_output)
- [x] **Kernel: spec** — `AgentSpec` + `MemberSpec` (pydantic, `extra="forbid"`), YAML
      loader (`safe_load`), `code:` Python-authoring hook. Test: load + reject unknown key.
      (db58329, proof: tests/test_spec.py::test_unknown_key_is_rejected)
- [x] **Kernel: context** — `RunContext` (user_id, session_id, run_id, agent_name;
      `memory_scope` = (user_id, agent_name)); `current_run` contextvar. Test: isolation
      across concurrent runs; caller session_id honored; run_id unique per turn.
      (97fe690, proof: tests/test_context.py::test_concurrent_runs_do_not_bleed_context)
- [x] **Kernel: engine contract** — `Engine` base + `EngineCapabilities`; `Registry[T]`
      with entry-point discovery; capability fail-fast at build. Test: unsupported spec
      raises a clear `CapabilityError`.
      (818ac02, proof: tests/test_engine.py::test_structured_output_on_echo_raises_capability_error)
- [x] **Echo engine** — trivial engine implementing run + stream. Test: run returns
      `echo: <input>`; stream yields ≥1 chunk then a terminal event.
      (1818327, proof: tests/test_echo.py::test_stream_yields_content_then_done)
- [x] **Runtime** — `build_agent(spec)` + `RunnableAgent.run/stream` with the middleware
      runner (on_request/on_response/on_error) and the capability gate. Test: middleware
      order (onion); on_error fires and re-raises; contextvar reset on all paths.
      (d65789c, proof: tests/test_runtime.py::test_contextvar_reset_after_early_break_of_stream)
- [x] **CLI** — `agentship run <file> --input ...` and `--stream`. Test: CLI runs the echo
      agent and prints output.
      (9ea6aba, proof: tests/test_cli.py::test_cli_run_prints_echo_output)
- [x] **Demo** — `examples/hello.yaml` + a README snippet; a test that runs it.
      (9ea6aba, proof: tests/test_cli.py::test_cli_stream_prints_echo_output)
- [x] **CI + pre-commit** — `.github/workflows/ci.yml` (offline suite on push/PR),
      `.pre-commit-config.yaml` (ruff + changed-area tests + files-per-commit cap).
      (ac25324, proof: .github/workflows/ci.yml runs `ruff check` + `pytest -q`)

> Note: all [x] above are proven by a named passing test in a green local suite
> (`pytest -q` → 30 passed, `ruff check` clean). Per operating-model.md the CI gate
> is only fully satisfied once these commits are pushed and the workflow runs green
> on the branch; there is no remote configured in this bootstrap yet.

## Proof
`pytest -q` green; `agentship run examples/hello.yaml --input hi` → `echo: hi`; CI green
on the pushed branch; every task above committed separately with its proving test named.

## Notes
This kernel is deliberately vendor-free so Phase 1+ adapters (LangGraph, LiteLLM, MCP,
mem0, OTel) attach to a stable base. The `echo` engine stays forever as the zero-dependency
conformance target every real engine is tested against.
