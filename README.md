# AgentShip demo

A small, **forkable** app built on [AgentShip](../agentship) that shows every
feature shipped so far. One runnable slice per feature, a test per slice, and a
single `make demo` that runs them all and prints a real result for each. You can also
just **talk to any agent in a browser chat** (`make ui`) — including the long-running
deep-research agent that pauses to ask "go deeper?" and resumes from its checkpoint.

**This demo is LIVE.** It needs a real API key and calls OpenAI for real, so it costs
a little each time you run it. No fakes, no echo stand-ins, no saved recordings, no
offline models — every slice makes a real call to the OpenAI API. The tests are live
too, with one deliberate exception: the deep-research pause/resume machinery (slice 8)
is proven by **deterministic offline tests**, because "the run paused, then resumed
after the human replied" is a control-flow guarantee, not model quality — a live model
can't reproduce it reliably. That slice also ships a live end-to-end test on top.

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

# ...or open a browser chat and talk to any agent (incl. the deep-research pause/resume):
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
| 8 | P02 | **Quick vs deep research (the many-speed ecosystem)** — a **coordinator** classifies a request and routes it to a fast single-turn **quick-search** agent (seconds, `durability: none`) or a long, iterative **deep-research** agent that runs several rounds, **pauses to ask "go deeper?"** (`interrupt`), and is **durable** (`durability: checkpoint`) so it survives a crash/long wait and resumes from the exact round to synthesize a report. The deep loop is authored natively in LangGraph. Drive it from a **browser chat** (`make ui`) — reply "yes"/"no" to the pause — or run it headless. | `agents/coordinator.yaml` · `agents/quick_search.yaml` · `agents/deep_research.yaml` · `demos/chat_ui.py` | `pytest tests/test_deep_research.py tests/test_chat_ui.py -q` |

> **Prerequisite for tests:** source your `.env` first so `OPENAI_API_KEY` is set.
> Without a key the live tests skip cleanly — they never fake-pass and never hard-error.
> Slice 8's offline deep-research tests run either way.

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
- **`deep-research`** (`agents/deep_research.yaml`) — a long, iterative agent authored natively in
  LangGraph (`deep_research/graph.py`). It plans sub-queries, searches, reflects and **deepens**
  for a few automatic rounds, then **pauses to ask the human "go deeper?"** (`interrupt`). Because
  it declares `durability: checkpoint`, every round is checkpointed — the run survives a crash or
  hours of waiting for the human and **resumes from the exact round**, then synthesizes a report.

**Drive it from a browser chat (recommended — no scripts):**

```bash
set -a; source ../agentship/.env; set +a   # OPENAI_API_KEY (+ optional BRAVE_API_KEY for real search)
make ui                                     # opens http://127.0.0.1:7860
```

Pick **deep-research**, send *"State of small modular reactors in 2026"* → it works a couple of
rounds, then the *"go deeper?"* question appears in the chat. Reply **yes** to dig another round
(it pauses again) or **no** to get the synthesized report. Switch the dropdown to **quick-search**
for a one-shot answer. The chat UI (`demos/chat_ui.py`) is generic — it drives *any* agent and
handles the pause/resume loop for you, on the same public `run`/`resume` API any caller would use.

**Or run it headless** — a coordinator classifies the request and routes to the right agent:

```bash
python demos/coordinated_research.py "Compare small modular reactor vendors in 2026"  # -> deep
python demos/coordinated_research.py "Who won the 2026 Super Bowl?"                    # -> quick
DEEP_APPROVE_ROUNDS=2 python demos/coordinated_research.py "State of EU AI regulation" # approve rounds
# or: make research INPUT="Compare small modular reactor vendors in 2026"
```

> Without `BRAVE_API_KEY`, each web search returns a clearly-labelled stub and the report is
> prefixed with an honest disclaimer that it is model knowledge, not grounded web findings — the
> depth/pause/resume machinery still runs end-to-end. Tune the automatic rounds before the pause
> with `DEEP_RESEARCH_AUTO_ROUNDS` (default 2).

| Test | What it proves |
|---|---|
| `tests/test_deep_research.py` | **Offline, deterministic:** N auto rounds → pause with a resume token + no output → resume declines → report; resume approves → another round; a **fresh engine resumes from the checkpoint** (the kill-9 guarantee); the stub disclaimer fires |
| `tests/test_chat_ui.py` | **Offline:** the chat handler drives ask → pause → yes → pause → no → report, and maps yes/no to the agent's resume value |
| `tests/test_coordinated_routing.py` | **Offline:** the coordinator's quick/deep label normalisation |
| `tests/test_coordinated_research.py` | **Live:** the real coordinator routes quick vs deep, and the deep path pauses then resumes to a real report |

---

## Run all slices at once

```bash
make demo
```

This runs slices 1–7 in order, prints a labeled block for each, and exits
non-zero if any slice fails — it is a real integration gate. The streaming slice
prints tokens as they arrive. (Slice 8, deep research, is interactive — it pauses for
a human — so you drive it from the chat UI (`make ui`) or `make research`, not `make demo`.)
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
exception is the deep-research pause/resume machinery (slice 8), covered by
deterministic **offline** tests (`test_deep_research.py`, `test_chat_ui.py`,
`test_coordinated_routing.py`) that run with or without a key; its live end-to-end
(`test_coordinated_research.py`) skips cleanly when no key is set.

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
    coordinator.yaml            # slice 8: thin classifier — labels a request quick or deep
    quick_search.yaml           # slice 8: the fast agent — single-turn web search (durability: none)
    deep_research.yaml          # slice 8: the long agent — native LangGraph loop (code: reference)
  deep_research/
    graph.py                    # slice 8: the durable, HITL deep-research StateGraph (build_agent)
    web_search.py               # slice 8: Brave web search (labelled stub when BRAVE_API_KEY unset)
  demos/
    run_all.py                  # `make demo` runner — runs slices 1–7 LIVE, prints each result
    chat_ui.py                  # `make ui` — generic browser chat for ANY agent, incl. pause/resume
    coordinated_research.py     # `make research` — headless: coordinator classifies, runs quick/deep
    ask_multiagent.py           # `make ask` — give the multi-agent panel your own task
  tests/
    conftest.py                 # requires_live_key skip guard + LiteLLM transport setup
    test_smoke.py               # slice 1: single template real answer (live)
    test_streaming.py           # slice 2: real gpt-4o-mini multi-token stream (live)
    test_graph.py               # slice 3: graph scaffold routed answer (live)
    test_custom.py              # slice 4: custom graph answer (live)
    test_router.py              # slice 5: router picks model + real turn runs (live)
    test_triage.py              # slices 6+7: happy path, kill-9 resume, parallel fan-out (live)
    test_deep_research.py       # slice 8: multi-round loop, pause, resume, disclaimer (offline)
    test_chat_ui.py             # slice 8: chat handler ask→pause→resume→report (offline)
    test_coordinated_routing.py # slice 8: coordinator quick/deep label normalisation (offline)
    test_coordinated_research.py# slice 8: real coordinator routes + deep pause→resume (live)
  pyproject.toml                # pins agentship[starter]==0.0.1 (future PyPI install)
  requirements-dev.txt          # editable-local install of the framework + gradio (dev mode)
  Makefile                      # install / test / demo / ui / research / run shortcuts
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
  (slices 4 and 6, and the deep-research loop in slice 8). Slice 8 also shows the
  durable, human-in-the-loop shape: `interrupt()` to pause + `durability: checkpoint`
  to resume from the exact round.

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
