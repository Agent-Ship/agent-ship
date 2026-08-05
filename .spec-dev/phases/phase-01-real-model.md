# Phase 1 — Real model (LiteLLM + LangGraph)  🔧 IN PROGRESS

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
- [ ] **T1 Deps + registration** — add the `[langgraph]` extra + fold into `[all]`; add
      `pytest-recording` to `[dev]`; register `langgraph` engine entry point. Proof: import
      + `Registry` resolves `langgraph`.
- [ ] **T2 Model seam** — `models.py::resolve_model` over `ChatLiteLLM` (thread
      temperature/max_tokens/api_base/timeout; drop `None`s; clear error on empty model).
      Proof: builds a configured model object; params thread; empty model raises `SpecError`.
- [ ] **T3 LangGraph engine** — single-agent graph; `run` returns the answer, `stream`
      yields token chunks + terminal `done`; capabilities declared; model injectable. Proof
      (offline, fake model): `run` returns the fake's output; `stream` yields ≥1 chunk then
      done; capability gate rejects `output:`/`members:` on this engine.
- [ ] **T4 Live-proof cassette** — vcr config (redact `authorization`), cassette dir, a
      gated live test that runs a real `gpt-4o-mini` through `build_agent` and asserts a
      non-empty answer; record the cassette; replay offline. Proof: the test passes in
      **replay** mode with no key.
- [ ] **T5 Demo** — `examples/assistant.yaml` (single real-model agent) + README snippet +
      a test that runs it (offline via fake, live via the cassette). Proof: demo test green.
- [ ] **T6 Track** — flip this file + `tasks.md` + Notion (P1 row) to done per task, with
      SHAs + proofs.

## Gaps (found during planning — fill or document)
- **G1 — one-time cassette recording needs a real provider key.** The active env has no
  provider key; `agent-ship/.env` holds `OPENAI_API_KEY`. Plan: record the cassette once
  using that key (a single cheap `gpt-4o-mini` call), commit it with auth redacted, then CI
  replays keyless. **If the key is missing/invalid at build time:** ship T3's offline
  fake-model proof + the gated live test, mark T4 as an OPEN gap here, and the cassette gets
  recorded on the first keyed run. (Update this line with the outcome.)
- (Add any further gaps the build surfaces here, then fill them.)

## Proof
`pytest -q` green (incl. the replayed cassette); `agentship run examples/assistant.yaml`
returns a real answer; every task committed separately with its named proving test.
