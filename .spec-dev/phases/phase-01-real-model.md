# Phase 1 — Real model (LiteLLM + LangGraph)  ✅ DONE

Goal: `agentship run` a single YAML agent and get a **real LLM answer**. Introduces the
LangGraph engine + a LiteLLM model seam, and establishes the **live-proof cassette**
pattern (record once with a key → replay offline in CI, no secrets).

## Deliverable (what must work)
```bash
agentship run examples/assistant.yaml --input "Name three primary colors."
# → a real model answer (offline suite proves the wiring with an injected fake model;
#   the live path is proven by a recorded, replayed cassette)
```

## Design notes
- **Packaging.** The default engine ships as the `[langgraph]` extra
  (`langgraph`, `langchain-litellm`, `langchain-core`), included in `[all]`. The kernel +
  `echo` engine stay dependency-light; a real run needs `pip install agentship[langgraph]`.
- **Model seam.** `agentship/models.py::resolve_model(model, **params) -> BaseChatModel`
  wraps `langchain_litellm.ChatLiteLLM`. It is a plain function, **not** a registry — we add
  a `ModelSource` seam only when a second implementation actually exists (principle: no
  speculative generality).
- **Engine.** `engines/langgraph/` builds a minimal single-agent graph (system prompt +
  user input → model → answer) with `streaming=True`; `tool_calling`/`structured_output`/
  `multi_agent` stay `False` (they arrive in phases 3/4/6, and the capability gate rejects
  them honestly until then). The model is resolved via `resolve_model` and is **injectable**
  so offline tests run with a fake chat model (no network).
- **Live proof.** `pytest-recording` (vcrpy) records the real HTTP round-trip once (auth
  headers redacted) into a committed cassette; CI replays it with no key. A one-time
  recording needs a real key (see Gaps).

## Tasks
- [x] **T1 Deps + registration** — add the `[langgraph]` extra + fold into `[all]`; add
      `pytest-recording` to `[dev]`; register `langgraph` engine entry point. Proof: import
      + `Registry` resolves `langgraph`. (091dc9d,
      `tests/test_langgraph_registration.py::test_langgraph_engine_is_registered_via_entry_point`)
- [x] **T2 Model seam** — `models.py::resolve_model` over `ChatLiteLLM` (thread
      temperature/max_tokens/api_base/timeout; drop `None`s; clear error on empty model).
      Proof: builds a configured model object; params thread; empty model raises `SpecError`.
      (2ff6115,
      `tests/test_models.py::test_resolve_model_builds_chatlitellm_with_model_and_temperature`)
- [x] **T3 LangGraph engine** — single-agent graph; `run` returns the answer, `stream`
      yields token chunks + terminal `done`; capabilities declared; model injectable. Proof
      (offline, fake model): `run` returns the fake's output; `stream` yields ≥1 chunk then
      done; capability gate rejects `output:`/`members:` on this engine. (ffd0708,
      `tests/test_langgraph_engine.py::test_run_returns_the_models_answer_through_the_graph`)
- [x] **T4 Live-proof cassette** — vcr config (redact `authorization`), cassette dir, a
      gated live test that runs a real `gpt-4o-mini` through `build_agent` and asserts a
      non-empty answer; record the cassette; replay offline. Proof: the test passes in
      **replay** mode with no key. (4076c4b,
      `tests/test_live_model.py::test_real_gpt_4o_mini_returns_a_non_empty_answer`)
- [x] **T5 Demo** — `examples/assistant.yaml` (single real-model agent) + README snippet +
      a test that runs it (offline via fake, live via the cassette). Proof: demo test green.
      (23af377,
      `tests/test_assistant_example.py::test_assistant_example_runs_offline_with_fake_model`)
- [x] **T6 Track** — flipped this file + `tasks.md` + Notion (P1 row) to done with SHAs +
      proofs; verified keyless replay (44 passed) and zero secrets in cassettes.
- [x] **T7 Clean run errors (G3)** — `agentship run` must never dump a raw traceback. Map
      provider/credential failures to a clear, actionable `ModelError`; the CLI prints a
      concise `Error: …` to stderr and exits 1 (a `--debug` flag re-raises the full trace).
      Proof: run against an engine that raises → clean message + exit 1, no traceback;
      missing-key message names the env var. (5c1d1e7,
      `tests/test_cli_errors.py::test_generic_error_shows_debug_hint_and_no_traceback`;
      credential mapping proven by
      `tests/test_cli_errors.py::test_credential_mapping_raises_model_error_naming_the_env_var`)

## Tasks (continued)
- [x] **T8 `.env` support for real runs (G4)** — the CLI loads a `.env` (via python-dotenv)
      so `agentship run` finds `OPENAI_API_KEY` without a manual export. Scoped to the CLI
      entry point ONLY (`cli.load_env_for_run`, called inside the `run` command — never at
      import / in tests) so tests stay hermetic and no stray paid calls. `--env-file`
      overrides (clean `Error:` if missing); `.env.example` shipped; the missing-key error now
      mentions the `.env` option; `python-dotenv` declared in core deps. Proof: with a `.env`
      in cwd the key is loaded (`ab46a2f`,
      `tests/test_cli_env.py::test_run_loads_dotenv_from_cwd`); a bare import does NOT load
      `.env` (`tests/test_cli_env.py::test_importing_agentship_does_not_load_dotenv`);
      `override=False` keeps an exported var
      (`tests/test_cli_env.py::test_run_does_not_override_an_exported_var`); missing
      `--env-file` is a clean error
      (`tests/test_cli_env.py::test_run_missing_env_file_is_a_clean_error`).

## Gaps (found during planning — fill or document)
- **G1 — one-time cassette recording — CLOSED.** Recorded on 2026-08-05 with the
  `OPENAI_API_KEY` from `agent-ship/.env` (single cheap `gpt-4o-mini` call each). Two
  cassettes committed with the `authorization` header redacted:
  `tests/cassettes/test_live_model/…` and `tests/cassettes/test_assistant_example/…`. Both
  replay with `OPENAI_API_KEY` UNSET (verified: `env -u OPENAI_API_KEY pytest -q` → 44
  passed). No secret is written to disk (`grep` for `sk-`/`Bearer` in the cassettes → 0).
- **G2 — LiteLLM ↔ vcrpy interception (found during build — CLOSED).** LiteLLM's async path
  defaults to an aiohttp transport vcrpy cannot intercept, and the OpenAI SDK refuses to
  build a client without a key even to replay. Resolved in the tests: set
  `litellm.disable_aiohttp_transport = True` (forces httpx so vcr hooks it),
  `LITELLM_LOCAL_MODEL_COST_MAP=True` (no stray cost-map fetch polluting the cassette), and
  a fixture that injects a placeholder key on keyless replay (VCR matches URI+body, not the
  redacted auth header).
- **G3 — raw traceback on a failed run (found by owner testing 2026-08-05 — CLOSED 5c1d1e7).**
  `agentship run examples/assistant.yaml` with no `OPENAI_API_KEY` dumped a ~100-line
  LiteLLM/LangGraph traceback (`openai.OpenAIError: Missing credentials`) instead of a clean
  message. Fixed in T7: `models.map_model_error` maps a credential failure to an actionable
  `ModelError` naming the env var (`OPENAI_API_KEY`); the LangGraph engine wraps its
  `run`/`stream` model call to raise it (chaining the cause); the CLI prints a single clean
  `Error: …` to stderr and exits 1, with `--debug` to re-raise the full traceback. LiteLLM's
  own stderr banner is silenced via `litellm.suppress_debug_info` so only our line shows.
  Verified keyless: `env -u OPENAI_API_KEY agentship run examples/assistant.yaml --input hi`
  → one clean `Error: No API credentials for model 'openai/gpt-4o-mini'. Set OPENAI_API_KEY …`
  and exit 1, no traceback.

- **G4 — no way to supply a real key for `agentship run` (asked by owner 2026-08-05 —
  CLOSED ab46a2f).** Tests replay cassettes keyless, but a real run needs a provider key
  and the CLI didn't load a `.env`, so the user had to `export` by hand. Fixed in T8:
  `cli.load_env_for_run` loads `./.env` (or `--env-file PATH`) with `override=False` so an
  exported var still wins; it is called *inside* the `run` command, never at import time, so
  the footgun (LiteLLM auto-loading `.env` and firing stray paid calls in tests) can't return.
  A hermeticity guard test proves a bare `import agentship` leaves the cwd `.env` unread
  (`tests/test_cli_env.py::test_importing_agentship_does_not_load_dotenv`); the keyless suite
  stays green (`env -u OPENAI_API_KEY pytest -q` → 53 passed). Verified real-run: a `.env` with
  a bogus `OPENAI_API_KEY` makes the run reach OpenAI and fail with a 401 (proving the key was
  loaded and sent), reported as one clean `Error:` line, not the "no credentials" message.
  Ships `.env.example`; `python-dotenv` added to core deps.

## Proof
`pytest -q` green (incl. the replayed cassette); `agentship run examples/assistant.yaml`
returns a real answer; every task committed separately with its named proving test.
