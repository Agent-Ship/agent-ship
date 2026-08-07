# Phase 1b — Model tuning + local models  🔧 IN PROGRESS

**New assertion (the delta over P1):** an agent can carry a `params:` block
(`temperature`, `max_tokens`, `timeout`) and an `api_base:` so generation is tunable and a
**local / self-hosted model** (Ollama, vLLM, any OpenAI-compatible server) works — the
params actually reach the provider call. (P1 only picked the model string; the engine
resolved it with *no* params.)

**Demo (what the user does):**
```bash
agentship run examples/tuned.yaml --input "..."     # temperature/max_tokens applied
agentship run examples/local.yaml  --input "..."     # talks to a local Ollama via api_base
```

## Package impact
- `agentship-core` — add `ModelParams` + `params:` to `AgentSpec` (`spec.py`).
- `agentship-langgraph` — thread `spec.params` into `resolve_model` (`engine.py:89`);
  `resolve_model` already accepts temperature/max_tokens/api_base/timeout (P1 T2).
- No new package, no new entry-point group.

## Tasks (one commit each; failing test first)
- [ ] **T1 `params:` on the spec (core)** — `ModelParams(BaseModel, extra="forbid")`
      (`temperature: float|None`, `max_tokens: int|None`, `api_base: str|None`,
      `timeout: float|None`) + `params: ModelParams | None = None` on `AgentSpec`. Proof:
      YAML with a `params:` block loads; an unknown param key raises `SpecError`; omitting
      `params` still works.
- [ ] **T2 thread params into the engine (langgraph)** — `engine.py` resolves
      `resolve_model(spec.model, **params)` where `params = spec.params.model_dump(exclude_none=True)`
      (or `{}`). Proof (offline): `build_agent` with `params` → the resolved model carries
      `temperature`/`api_base` (monkeypatch `resolve_model` to capture kwargs); no `params`
      → no kwargs passed.
- [ ] **T3 examples + local-model docs** — `examples/tuned.yaml` (temperature+max_tokens) and
      `examples/local.yaml` (`model: ollama/llama3`, `api_base: http://localhost:11434`);
      a "Local & self-hosted models" section in `examples/README.md`. Proof: both examples
      build; `local.yaml`'s `api_base` threads to the model (offline capture).
- [ ] **T4 Track** — flip this file + `tasks.md` + Notion (P1b row) with SHAs + proofs.

## User-facing states
- Bad param type (e.g. `temperature: "hot"`) → pydantic error → `SpecError` via `load_spec`.
- Unknown param key → `extra="forbid"` → `SpecError` (loud, not silent).
- Dead `api_base` at run time → the P1 `map_model_error` generic branch → clean
  `Error: Model call failed for '<model>': ...` (no traceback). Add a test asserting a
  connection failure surfaces as `ModelError`, not a raw stack.
- Local model needs **no API key** — the credential-error mapping must NOT fire for a local
  `api_base` run (assert a local-model path doesn't demand `OPENAI_API_KEY`).

## Secrets / credentials & hermeticity
No new secrets. Local models need no key. **No new paid calls / no new cassette** — param
threading is proven OFFLINE (capture `resolve_model` kwargs). A real Ollama isn't in CI, so
`local.yaml` is proven by the offline api_base-threads assertion + a documented manual test
(`ollama serve && ollama pull llama3` then `agentship run examples/local.yaml`). Tests stay
hermetic (no network).

## Live-proof plan
Offline only (mechanism: params reach the provider call). P1's cassette already proves the
real hosted call; 1b adds no live path that CI must replay.

## Gaps (log during build, then fill)
- (none yet)

## Proof
`env -u <keys> pytest packages -q` green; `agentship run examples/tuned.yaml` applies params;
`examples/local.yaml` documented + api_base-threading tested; every task its own commit with
a named proving test; demo repo unaffected (its smoke test stays green — it uses no params).
