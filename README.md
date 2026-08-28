# AgentShip demo

A small, **forkable** app built on [AgentShip](../agentship) that shows every
feature shipped so far. One runnable slice per feature, a test per slice, and a
single `make demo` that runs them all and prints a real result for each. You can also
**drive every agent from one browser chat** (`make ui`) — pick an agent, send input,
and watch it work: the multi-agent supervisors show their classify → route → dispatch
path in a Trace panel, and the durable note-taker agent pauses to ask for write approval
and resumes from its checkpoint, all in the conversation.

**The demos are LIVE; the tests replay recordings.** `make demo` needs a real API key and
calls OpenAI for real — no fakes, no echo stand-ins, no offline models. That is the point
of the demo scripts, and it hasn't changed.

The **test suite** is a different tier on purpose. `make test` replays committed VCR
cassettes: **no key, no network, no spend, ~3 seconds.** That's what a fresh clone gets and
what CI gates on. The recordings are of real provider round-trips — the same test bodies
that run live — so a green replay is real evidence, not a stub passing against itself.
Credentials are redacted on write, so a committed cassette leaks nothing.

| Command | What it does | Needs a key? |
|---|---|---|
| `make test` | Replays cassettes. The CI gate. | No |
| `make test-live` | Same tests, real API calls. Catches provider drift. | Yes |
| `make record` | Re-records the cassettes after a behaviour change. | Yes |
| `make demo` | The live showcase — real calls, printed results. | Yes |

A test whose cassette is missing **fails loudly** rather than silently reaching for the
network, so the keyless tier can never quietly degrade into a live one. Three tests remain
live-only and skip by default: the Opik / LangFuse / LangSmith trace read-backs, where the
assertion is "the vendor's API now shows our span" and there is nothing local to record.

New slices arrive one per phase as the framework ships each feature.

---

## Quick start

```bash
# 1. Copy the credentials template and fill in your key
cp .env.example .env
# edit .env and set: OPENAI_API_KEY=sk-...

# 2. Create a virtual env and install dependencies
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt   # or: make install

# 3. Run every slice live
make demo

# ...or open one browser chat to drive EVERY agent (routing trace + note-taker pause/resume):
make ui   # http://127.0.0.1:7860
```

---

## What this demo shows

Live slices, one per shipped capability. Each slice is an agent spec under
`agents/` with a corresponding test under `tests/`. `make demo` runs the core slices
top to bottom.

| # | Phase | Feature | Agent spec | Run just this slice |
|---|---|---|---|---|
| 1 | P01 | **`template: single`** — zero author code; one real `gpt-4o-mini` turn | `agents/assistant.yaml` | `pytest tests/test_smoke.py -q` |
| 2 | P01 | **Streaming** — real token-by-token stream; >1 chunk arrives live | `agents/streaming.yaml` | `pytest tests/test_streaming.py -q` |
| 3 | P01 | **`template: graph`** — supervisor scaffold; coordinator routes → worker answers | `agents/graph.yaml` | `pytest tests/test_graph.py -q` |
| 4 | P01 | **Custom `build_graph`** — the author's own native LangGraph graph answers | `agents/custom/custom.yaml` | `pytest tests/test_custom.py -q` |
| 5 | P01 | **`ModelRouter`** — router picks the model id, then a real turn runs | _(spec built inline)_ | `pytest tests/test_router.py -q` |
| 6 | P02 | **Durable multi-agent supervisor** — 1 supervisor + 3 real sub-agents; classify → route to **one** sub-agent → resolve; then a **fresh engine resumes from the checkpoint and produces byte-identical output** (the kill-9 guarantee, actually proven). The classify → route → dispatch → resolve trace prints inline so the routing is visible. | `agents/triage/triage.yaml` | `pytest tests/test_triage.py -q` |
| 7 | P02 | **Multi-agent fan-out** — one question dispatched to **all 3 sub-agents concurrently** (`strategy: parallel`), then the `ConflictResolver` merges their competing answers by priority. You watch several real sub-agents run at once and get reconciled. | `agents/triage/panel.yaml` | `pytest tests/test_triage.py -q` |
| 8 | P02 | **Quick vs deep research (the many-speed ecosystem)** — a fast single-turn **quick-search** agent (seconds, `durability: none`) and a real **model-driven deep-research** agent (`template: single` ReAct, `durability: checkpoint`, `tools: [web_search, scrape_url]`): say "hi" and it just greets you (no search, no pause); ask a substantive question and it runs several `web_search` calls from different angles, `scrape_url`s the best sources to read their full content, cross-checks, and writes a cited answer. Because it's durable and the chat reuses one `session_id`, it remembers the conversation and a long run survives a crash/wait and resumes. Drive both from the **browser chat** (`make ui`). | `agents/quick_search.yaml` · `agents/deep_research.yaml` · `demos/chat_ui.py` | `pytest tests/test_deep_research.py tests/test_chat_ui.py -q` |
| 9 | P07 | **Observability — a full trace from one YAML block** — add an `observability:` block and the runtime resolves it to a real OpenTelemetry observer, so a live tool-calling turn exports a nested span tree (**agent → node → model → tool**) carrying tokens, cost, and latency. The default `console` exporter prints the tree to **stderr** (stdout stays the clean answer); switch `exporters:` to **Opik / LangFuse / LangSmith** and the same trace ships to that hosted backend. The test reads the trace **back** from each backend's own API to prove it landed — skipping any backend whose keys aren't set. | `agents/observability.yaml` · `demos/observability.py` | `pytest tests/test_observability.py -q` |

> **Prerequisite for tests:** source your `.env` first so `OPENAI_API_KEY` is set.
> Without a key the live tests skip cleanly — they never fake-pass and never hard-error.
> Slice 8's `test_chat_ui.py` runs offline either way.

---

## Give the multi-agent panel your own task (one command)

Hand it a task and *watch the sub-agents get called* — straight from the `agentship`
CLI with `--verbose`:

```bash
agentship run agents/triage/panel.yaml \
  --input "I was overcharged on my invoice and I keep getting headaches" --verbose
```

`--verbose` prints the supervisor's decision trace to **stderr** (so stdout stays just
the final answer, safe to pipe). It fans your question out to all three sub-agents
concurrently and shows each named sub-agent's live answer, then the resolver's pick:

```
  · classify: '...' -> intent=billing
  · dispatch: parallel -> sub-agents ['billing_specialist', 'clinical_specialist', 'faq_specialist']
  ·   billing_specialist  (sub-agent) -> I'm sorry to hear about the overcharge ...
  ·   clinical_specialist (sub-agent) -> It sounds like you're facing two separate issues ...
  ·   faq_specialist      (sub-agent) -> I'm sorry to hear about the overcharge and your headaches ...
  · resolve: winner=billing_specialist considered=[all three] dropped=[]
I'm sorry to hear about the overcharge on your invoice. Please review your billing ...   <- stdout
```

Route to just **one** sub-agent instead of fanning out? Point at `triage.yaml`:

```bash
agentship run agents/triage/triage.yaml --input "My invoice is wrong" --verbose
```

### Zero-Python supervisor (sub-agents wired in YAML)

`triage.yaml` and `panel.yaml` wire their sub-agents with a small Python `code:` factory
(for full control — custom routing, parallel fan-out). If you'd rather write **no Python
at all**, `triage_declarative.yaml` lists its sub-agents right in YAML via `members:`, each
a `ref:` to a specialist YAML — the old supervisor-plus-sub-agent-folder layout, fully
declarative:

```yaml
name: triage-declarative
engine: langgraph
model: openai/gpt-4o-mini
durability: checkpoint
members:
  - name: billing_specialist
    ref: specialists/billing.yaml
    description: billing, invoices, double charges, payments, and refunds
  - name: clinical_specialist
    ref: specialists/clinical.yaml
    description: health, symptoms, and medical questions
  - name: faq_specialist
    ref: specialists/faq.yaml
    description: general questions about the service
```

The framework resolves the refs, derives the routing from each member's `description:`,
and coordinates the same classify → route → dispatch → resolve turn — no factory:

```bash
agentship run agents/triage/triage_declarative.yaml \
  --input "My invoice looks wrong — who handles payments?" --verbose
```

| Authoring style | File | When to use |
|---|---|---|
| **Zero Python** — `members:` ref sub-agent YAMLs | `triage_declarative.yaml` | Simplest; classify-and-route over a set of specialists |
| **`code:` factory** — full control | `triage.yaml` / `panel.yaml` | Custom routing, parallel fan-out, retry, HITL |

`make ask INPUT="..."` wraps the same panel run, and `make demo-multiagent` gives a
red/green assertion that all three sub-agents were dispatched + considered.

## Running each slice

### 1 — `template: single`

```bash
agentship run agents/assistant.yaml --input "Give one productivity tip in one short sentence."
# or via make:
make run INPUT="Give one productivity tip."
```

Expected output: a short, non-empty tip from `gpt-4o-mini`.

### 2 — Streaming

```bash
agentship run agents/streaming.yaml --input "Name the 8 planets, comma-separated." --stream
```

Expected output: tokens arrive one by one, then the fully reassembled answer.

### 3 — `template: graph` (supervisor scaffold)

```bash
agentship run agents/graph.yaml --input "Help me plan a weekend trip to the mountains."
```

Expected output: the coordinator routes the request to a worker specialist and the
worker answers. **This is the authoring scaffold.** The full durable multi-agent
runtime (checkpoints, resume, retry, HITL) is slice 6.

### 4 — Custom `build_graph`

```bash
# Run from the demo repo root so the repo-root-relative code: path resolves
agentship run agents/custom/custom.yaml --input "Name three primary colors."
```

Expected output: the *author's* own LangGraph graph (not a template) answers. Shows
that a `code:` factory hands full control to the author — the harness just wires the
model and drives `run`/`stream`.

### 5 — `ModelRouter`

The router slice is exercised via `make demo` or its test (no standalone YAML for
this one — the spec is built in the demo runner directly):

```bash
pytest tests/test_router.py -q
```

Expected output: the `DefaultModelRouter` picks `openai/gpt-4o-mini`, then a real
turn through that model answers. The default router is a simple pass-through today;
the full lookup router (tier → model table) lands in Phase 03.

### 6 — Durable multi-agent supervisor (Phase 02)

```bash
# Run from the demo repo root so the repo-root-relative code: path resolves
agentship run agents/triage/triage.yaml \
  --input "My invoice looks wrong and I was double charged — who handles payments?"
```

`make demo` runs the full slice including the resume step (see below).

> **The sub-agents are real, and each is its own YAML file.** Every specialist lives in
> `agents/triage/specialists/` as a standalone `template: single` agent YAML
> (`billing.yaml`, `clinical.yaml`, `faq.yaml`) — you can run any of them on their own:
> ```bash
> agentship run agents/triage/specialists/billing.yaml --input "I was double charged."
> ```
> `build_triage_supervisor()` loads those YAMLs by path (`build_agent(".../billing.yaml")`),
> so each specialist is a full, independent agent with its own graph and thread. At runtime
> the supervisor's `dispatch` node resolves the routed name and invokes that agent through
> the public `run` seam — a genuine separate sub-agent turn, not an inlined prompt. This
> mirrors the old agent-ship layout (a supervisor + a folder of sub-agent YAMLs). The
> supervisor logs each decision on the `agentship.supervisor` logger; the demo enables it
> so you see exactly which sub-agent handled the request.

**What `make demo` slice 6 prints — the routing is now visible:**

```
supervisor built 3 sub-agents: billing_specialist, clinical_specialist, faq_specialist
      | classify: 'My invoice looks wrong ...' -> intent=billing
      | route: intent=billing -> specialists=['billing_specialist'] strategy=single
      | dispatch: single -> sub-agents ['billing_specialist']
      |   billing_specialist (sub-agent) -> For billing discrepancies like being double charged ...
      | resolve: winner=billing_specialist considered=['billing_specialist'] dropped=[]
[1] original answer: For billing discrepancies like being double charged ...
    resume_token minted by 'langgraph', thread='demo-triage-resume'
[2] simulating kill -9 → fresh LangGraphEngine, no shared state …
[3] resumed answer:  For billing discrepancies like being double charged ...
✓ byte-identical — checkpoint replayed identically.
```

The resume step is not a claim — `slice_triage` in `demos/run_all.py` actually:
1. Runs the supervisor on a fixed `session_id` to get a real answer + resume token.
2. Instantiates a brand-new `LangGraphEngine` (equivalent to a process restart).
3. Calls `engine.resume(new_compiled, token, ctx)` with only the token — no shared state.
4. Asserts the output is byte-identical to the original.

In dev (no `AGENT_SESSION_STORE_URI` set) the in-memory saver singleton acts as the
checkpoint store. In production with Postgres the same guarantee holds across a real process kill.

**What this proves end-to-end (Phase 02):**
- **Multi-agent** (C1): classify → route → dispatch fan-out → resolve.
- **Durable checkpoints** (C4): checkpointed per node; kill → fresh engine → resume → identical output, *actually demonstrated*.
- **Bounded retry** (C6): retryable specialist failures retry up to the configured cap.
- **Dispatch strategies** (C7): `first-wins`, `all`, `fastest` — `first-wins` is the triage default.
- **Thread locking** (C8): exactly one worker can hold a thread at a time (`ThreadBusyError` on contention).

**Two live tests for slice 6:**

```bash
set -a; source ../agentship/.env; set +a
pytest tests/test_triage.py -q -s    # runs both tests below
```

| Test | What it proves |
|---|---|
| `test_triage_routes_and_answers_a_billing_question_live` | Happy path: classify → route → real answer + resume token minted |
| `test_triage_resume_after_simulated_kill` | Kill-9 guarantee: fresh engine resumes from token → byte-identical output |

### 7 — Multi-agent fan-out (parallel sub-agents + ConflictResolver)

```bash
# Run from the demo repo root so the repo-root-relative code: path resolves
agentship run agents/triage/panel.yaml \
  --input "My latest bill looks wrong and I've also been feeling dizzy — can you help?"
```

Where slice 6 routes to **one** sub-agent, the panel (`build_triage_panel()`) fans
**every** request out to **all three** sub-agents concurrently (`strategy: parallel`),
then the `ConflictResolver` merges their competing answers by priority
(`billing > clinical > faq`). This is the clearest "multiple sub-agents" demo — you
watch three real agents run at once and get reconciled deterministically:

```
      | classify: "My latest bill looks wrong ..." -> intent=billing
      | route: intent=billing -> specialists=['billing_specialist', 'clinical_specialist', 'faq_specialist'] strategy=parallel
      | dispatch: parallel -> sub-agents ['billing_specialist', 'clinical_specialist', 'faq_specialist']
      |   billing_specialist (sub-agent) -> For your billing concern ...
      |   clinical_specialist (sub-agent) -> While I can't assist with billing ...
      |   faq_specialist (sub-agent) -> I can help with your billing question ...
      | resolve: winner=billing_specialist considered=[all three] dropped=[]
  -> merged winner's answer: For your billing concern ...
```

Its live test asserts on the captured decision log — that a `parallel` dispatch ran
all three named sub-agents and the resolver *considered* all three before picking one:

| Test | What it proves |
|---|---|
| `test_triage_panel_fans_out_to_multiple_sub_agents_in_parallel` | Fan-out is real: 3 named sub-agents dispatched in parallel, all 3 considered by the resolver |

### 8 — Quick vs deep research (the many-speed ecosystem)

Two agents at opposite ends of the speed spectrum:

- **`quick-search`** (`agents/quick_search.yaml`) — a single ReAct turn: search, answer, done.
  `durability: none`, because a quick lookup has nothing worth checkpointing.
- **`deep-research`** (`agents/deep_research.yaml`) — a real **model-driven** agent
  (`template: single` ReAct, `durability: checkpoint`, `tools: [web_search, scrape_url]`) with a
  research-grade prompt. **The model decides what to do:** say "hi" and it just greets you (no
  search, no pause); ask a substantive question and it runs several `web_search` calls from
  different angles, `scrape_url`s the most promising sources to read their full content,
  cross-checks them, and writes a cited answer. It is **not** a hardcoded pipeline and does not
  force a "go deeper?" pause. Because it declares `durability: checkpoint` and the chat reuses one
  `session_id` across turns, it **remembers the conversation** and a long run survives a crash or a
  long wait and resumes.

**Drive it from a browser chat (recommended — no scripts):**

```bash
set -a; source ../agentship/.env; set +a   # OPENAI_API_KEY (+ optional FIRECRAWL_API_KEY)
make ui                                     # opens http://127.0.0.1:7860
```

Pick **deep-research**, send *"hi"* → it just greets you (no search). Send *"State of small modular
reactors in 2026"* → it runs several web searches, opens the best sources with `scrape_url`,
cross-checks, and returns a cited answer.

The chat UI (`demos/chat_ui.py`) is the **one interactive front door for every agent** — the
dropdown lists all of them (deep-research, quick-search, triage, triage panel, note-taker HITL,
assistant, calculator, streaming, graph, custom, autonomous). Pick any, send input, watch it
work. A collapsible **Trace** panel shows AgentShip's own decision log for the turn, so the
multi-agent supervisors' **classify → route → dispatch → resolve** path is visible instead of
hidden behind a single reply. It's all on the same public `run`/`resume` API any caller would use;
build/run failures are shown in the chat rather than crashing the app.

> With `FIRECRAWL_API_KEY` (free at firecrawl.dev) or `BRAVE_API_KEY`, `web_search`/`scrape_url`
> return real results; without a key each returns a clearly-labelled setup message and the model
> answers from its own knowledge — the agent still runs end-to-end.

The **durable pause/resume** guarantee is showcased by the **note-taker HITL** agent
(`agents/hitl/agent.yaml`) — see slice 6 (HITL) and section 6 of `MANUAL_TESTING.md`.

| Test | What it proves |
|---|---|
| `tests/test_deep_research.py` | **Live** (skips without `OPENAI_API_KEY`): deep-research answers "hi" with a normal reply and runs **no web search** — the old force-pause bug, encoded as a regression guard |
| `tests/test_chat_ui.py` | **Offline** (fake chat model): every picker agent builds; yes/no maps to `{"approved": bool}` for a confirm-write and other text passes through; the write-approval prompt renders; a real agent answers with a stable `session_id` across turns; the UI holds a resume token then resumes a paused run; a build failure is shown in chat not crashed |

### 9 — Observability: a full trace from one YAML block (Phase 07)

`agents/observability.yaml` is the calculator agent with one thing added — an `observability:`
block:

```yaml
observability:
  provider: otel
  exporters: [console]     # swap in: opik, langfuse, langsmith
  capture_content: true
```

That block is the whole feature. The runtime resolves it to a real OpenTelemetry observer (via the
`agentship.observers` entry point), so every turn now exports a **nested span tree** —
`agent → node → model → tool` — with **tokens, cost, and latency** on the model spans. No author
code, no client wiring.

```bash
set -a; source .env; set +a          # OPENAI_API_KEY (+ any backend keys, below)
make demo-observability              # or: python demos/observability.py
```

With the default `console` exporter the tree prints to **stderr** — stdout stays the clean answer,
so a piped run is never polluted by trace output. Point it at a hosted backend by changing
`exporters:` and setting that backend's keys:

| Backend | Env vars | Notes |
|---|---|---|
| **Opik** | `OPIK_API_KEY`, `OPIK_WORKSPACE`, `OPIK_PROJECT_NAME`, (`OPIK_OTEL_ENDPOINT` for self-host) | OTLP/HTTP endpoint |
| **LangFuse** | `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, (`LANGFUSE_HOST`) | OTLP/HTTP, HTTP Basic auth |
| **LangSmith** | `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT` | ships spans off-box → needs `allow_saas_exporter: true` |

All three are **hosted** (API keys, nothing to self-deploy). Keys live in `.env` (never in code or
YAML); the exporters read them from the environment at build time.

| Test | What it proves |
|---|---|
| `tests/test_observability.py::test_declarative_block_gives_a_live_traced_turn` | **Live** (skips without `OPENAI_API_KEY`): building straight from the YAML yields a real (non-no-op) observer and a live tool-calling turn answers correctly — the published, zero-code declarative path |
| `::test_full_trace_exports_to_{opik,langfuse,langsmith}` | **Live read-back** (skips unless that backend's keys are set): exports the same turn over real OTLP, then queries the backend's **own API** and asserts the full `agent → node → model → tool` tree landed — not a fake pass, the trace is confirmed on the backend |

---

## Run all slices at once

```bash
make demo
```

This runs slices 1–7 in order, prints a labeled block for each, and exits
non-zero if any slice fails — it is a real integration gate. The streaming slice
prints tokens as they arrive. (Slice 8's quick-search and deep-research agents are best
driven interactively from the chat UI (`make ui`), not `make demo`.)
Example output:

```
========================================================================
  [1] template: single (real gpt-4o-mini)
      LIVE · real answer · zero author code
========================================================================
  -> Batch similar tasks together to reduce context-switching.

========================================================================
  [6] durable multi-agent supervisor (Phase 02 killer demo)
      LIVE · classifies, routes to a specialist, resolves — durable (resume token minted)
========================================================================
  [1] original answer: Our billing team handles payment issues ...
      resume_token minted by 'langgraph', thread='demo-triage-resume'
  [2] simulating kill -9 → fresh LangGraphEngine, no shared state …
  [3] resumed answer:  Our billing team handles payment issues ...
  ✓ byte-identical — checkpoint replayed identically.

DEMO OK — every capability ran LIVE against OpenAI.
```

---

## Run all tests

```bash
set -a; source ../agentship/.env; set +a
pytest -q        # or: make test
```

Most tests call OpenAI live — without a key they skip, and never fake-pass. The
exception is the UI plumbing (slice 8), covered by the deterministic **offline**
`test_chat_ui.py` (a fake chat model) that runs with or without a key; the live
`test_deep_research.py` (deep-research answers "hi" without searching) skips cleanly
when no key is set.

---

## What's here

```
agentship-demo/
  agents/
    assistant.yaml              # slice 1: template: single — one real-model assistant
    streaming.yaml              # slice 2: real gpt-4o-mini streamed token by token
    graph.yaml                  # slice 3: template: graph — supervisor scaffold
    custom/
      custom.yaml               # slice 4: custom build_graph spec (code: reference)
      agent.py                  # slice 4: the native LangGraph agent it points at
    triage/
      triage.yaml               # durable supervisor spec — routes to ONE sub-agent (code: factory)
      panel.yaml                # fan-out panel spec — parallel to ALL sub-agents (code: factory)
      triage_declarative.yaml   # ZERO-Python supervisor — members: ref the sub-agent YAMLs directly
      agent.py                  # build_triage_supervisor / build_triage_panel — loads specialist YAMLs
      specialists/              # each sub-agent is its OWN YAML (a standalone template: single agent)
        billing.yaml            #   billing_specialist — also runnable on its own
        clinical.yaml           #   clinical_specialist — also runnable on its own
        faq.yaml                #   faq_specialist — also runnable on its own
    quick_search.yaml           # slice 8: the fast agent — single-turn web search (durability: none)
    deep_research.yaml          # slice 8: the deep agent — model-driven ReAct + web_search + scrape_url (durability: checkpoint)
    hitl/
      agent.yaml                # note-taker HITL — confirm_writes: true, pauses for approval before save_note
  demos/
    run_all.py                  # `make demo` runner — runs slices 1–7 LIVE, prints each result
    chat_ui.py                  # `make ui` — one browser chat for EVERY agent: trace panel + pause/resume
    ask_multiagent.py           # `make ask` — give the multi-agent panel your own task
  tests/
    conftest.py                 # requires_live_key skip guard + LiteLLM transport setup
    test_smoke.py               # slice 1: single template real answer (live)
    test_streaming.py           # slice 2: real gpt-4o-mini multi-token stream (live)
    test_graph.py               # slice 3: graph scaffold routed answer (live)
    test_custom.py              # slice 4: custom graph answer (live)
    test_router.py              # slice 5: router picks model + real turn runs (live)
    test_triage.py              # slices 6+7: happy path, kill-9 resume, parallel fan-out (live)
    test_deep_research.py       # slice 8: deep-research answers "hi" with NO web search (live)
    test_chat_ui.py             # slice 8: every picker agent builds, resume-token plumbing, memory (offline)
    test_hitl_write.py          # slice 6: note-taker pauses → approve → write fires once (live)
  pyproject.toml                # pins agentship[starter]==0.0.1 (future PyPI install)
  requirements-dev.txt          # editable-local install of the framework + gradio (dev mode)
  Makefile                      # install / test / demo / ui / run shortcuts
  .env.example                  # credentials template — copy to .env and set OPENAI_API_KEY
  .github/workflows/ci.yml      # runs the live tests (needs the OPENAI_API_KEY repo secret)
```

---

## Authoring options

The slices exercise AgentShip's three authoring paths:

- **`template: single`** — zero author code (slice 1, `assistant.yaml`).
- **`template: graph`** — fillable multi-agent supervisor scaffold (slice 3).
- **Custom `build_graph`** — full control: subclass `LangGraphAgent`, write native
  LangGraph in `build_graph(model, tools)`, reference via `code:`. The harness wires
  the model and drives `run`/`stream`; the author never touches a vendor SDK
  (slices 4 and 6). The durable, human-in-the-loop shape — the framework pauses before
  a side-effecting write for approval + `durability: checkpoint` to resume — is shown by
  the note-taker HITL agent (`agents/hitl/agent.yaml`).

---

## Install

### Now (dev mode — editable-local)

AgentShip is not on PyPI yet; install the framework editable from the sibling monorepo
checkout. This repo expects the framework at `../agentship`.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
# or, equivalently:
make install
```

### Later (pinned pip — once AgentShip is published)

```bash
pip install "agentship[starter]==0.0.1"
```

When that works the editable-local step and the sibling-checkout step in CI go away.
