# AgentShip demo

A small, **forkable** app built on [AgentShip](../agentship) that shows **every
capability shipped through Phase 00-01** — one runnable, keyless slice per feature,
plus a single `make demo` that runs them all and prints a labeled result for each.
The point is that you can literally *see* every feature run: `make demo`.

Its job is also to prove the *published* framework actually works end to end: fork
it, install AgentShip by version, and run real agents. Every slice runs with **no
API key** — the offline slices (echo, router, deepagents) run against
the kernel or a fake model; the real-model slices (streaming, single, graph, custom) replay a
committed, redacted VCR cassette. That keyless suite is the anti-rot guard in CI:
it catches packaging breakage (missing entry point, unwired extra, an import that
only resolves from the source tree) the moment it happens.

New slices arrive **one per phase** as the framework ships each capability.

## What this demo shows

Every Phase 00-01 capability, with the slice that demonstrates it, how to run it,
and what you see. `make demo` runs the whole column top to bottom.

| # | Capability | Slice artifact | Run it | What you see | Test this slice (no key) |
|---|---|---|---|---|---|
| 1 | **Echo walking skeleton** (keyless kernel) | `agents/echo.yaml` | `agentship run agents/echo.yaml --input "hi"` | `echo: hi` — no model, no key | `env -u OPENAI_API_KEY python -m pytest tests/test_echo.py -q` |
| 2 | **Streaming** (real `gpt-4o-mini`) | `agents/streaming.yaml` + `--stream` | `agentship run agents/streaming.yaml --input "Name the 8 planets." --stream` | the answer arrives as **multiple real tokens** — `chunk#1 'Merc'`, `chunk#2 'ury'`, … (18 chunks) — reassembling to the full answer (replayed cassette). The echo `--stream` path stays only as the zero-dependency skeleton. | `env -u OPENAI_API_KEY python -m pytest tests/test_streaming.py::test_demo_streams_multiple_real_tokens -q` |
| 3 | **`template: single`** (zero author code) | `agents/assistant.yaml` | `agentship run agents/assistant.yaml --input "Give one productivity tip."` | a real `gpt-4o-mini` answer (replayed cassette) | `env -u OPENAI_API_KEY python -m pytest tests/test_smoke.py::test_demo_assistant_returns_a_non_empty_answer -q` |
| 4 | **`template: graph`** (supervisor scaffold) | `agents/graph.yaml` | `agentship run agents/graph.yaml --input "Help me plan a trip."` | coordinator routes → worker answers (replayed cassette). **Scaffold only — durable multi-agent runtime = Phase 02.** | `env -u OPENAI_API_KEY python -m pytest tests/test_graph.py::test_graph_scaffold_routes_and_returns_a_non_empty_answer -q` |
| 5 | **`template: deepagents`** (prebuilt) | `agents/deepagents.yaml` | `agentship run agents/deepagents.yaml --input "..."` | `deepagents graph compiled: <type>`. **Compiles only today — autonomous tool-using turn = Phase 03.** | `env -u OPENAI_API_KEY python -m pytest tests/test_deepagents.py::test_deepagents_template_compiles_into_a_deep_agent_graph -q` |
| 6 | **Custom `build_graph`** (native LangGraph) | `agents/custom/custom.yaml` + `agents/custom/agent.py` | `agentship run agents/custom/custom.yaml --input "Name three primary colors."` | the *author's* graph answers (replayed cassette) | `env -u OPENAI_API_KEY python -m pytest tests/test_custom.py::test_custom_build_graph_answers_via_the_authors_graph -q` |
| 7 | **`ModelRouter`** (routing mechanism) | `tests/test_router.py` / `demos/run_all.py` | `make demo` (slice 7) | `DefaultModelRouter` picks the id, the route step stamps `ctx.routed_model`, the engine adapter reads it — no LLM call | `env -u OPENAI_API_KEY python -m pytest tests/test_router.py -q` |

Each slice ships its artifact under `agents/` (YAML, or `agents/custom/` for the
code path), a **keyless test** under `tests/` that asserts its real
output/behavior, and an entry in the `make demo` runner.

**Test one slice vs. run all.** Each row's last column is a copy-paste command that
runs *only that slice's* test, keyless — target a single test node with the
`tests/<file>::<node>` pattern (the `-u OPENAI_API_KEY` unsets the key to prove it
needs none). To run **every** slice's test at once, use the whole keyless suite:

```bash
env -u OPENAI_API_KEY python -m pytest -q     # or: make test
```

```bash
make demo    # runs every slice keyless; prints a labeled block per capability
             # exits non-zero if any slice fails
```

Honest labels are enforced, not decorative: `graph` is the authoring *scaffold*
(the durable multi-agent runtime lands in Phase 02); `deepagents` *builds/compiles*
today (a full autonomous tool-using turn lands in Phase 03). Neither slice claims a
capability that isn't shipped.

## What's here

```
agentship-demo/
  agents/
    echo.yaml               # slice 1: echo walking skeleton (run), keyless
    streaming.yaml          # slice 2: real gpt-4o-mini streamed token by token
    assistant.yaml          # slice 3: template: single — one real-model assistant
    graph.yaml              # slice 4: template: graph — supervisor scaffold
    deepagents.yaml         # slice 5: template: deepagents — prebuilt (compiles only)
    custom/
      custom.yaml           # slice 6: custom build_graph spec (code: reference)
      agent.py              # slice 6: the native LangGraph agent it points at
  demos/
    run_all.py              # `make demo` runner — runs every slice, prints each result
  tests/
    conftest.py             # VCR config — redacts credentials, replays keyless
    test_echo.py            # slice 1: echo run + stream skeleton (keyless)
    test_streaming.py       # slice 2: real gpt-4o-mini multi-token stream (replayed cassette)
    test_smoke.py           # slice 3: single template real answer (replayed cassette)
    test_graph.py           # slice 4: graph scaffold routed answer (replayed cassette)
    test_deepagents.py      # slice 5: deepagents compiles (offline, fake model)
    test_custom.py          # slice 6: custom graph answer (replayed cassette)
    test_router.py          # slice 7: ModelRouter route→stamp→read mechanism (keyless)
    cassettes/              # committed, redacted HTTP recordings (replayed in CI)
  pyproject.toml            # pins agentship[starter]==0.0.1 (future PyPI install)
  requirements-dev.txt      # editable-local install of the framework (dev mode)
  Makefile                  # install / test / demo / run shortcuts
  .env.example              # credentials template — copy to .env and fill in
  .github/workflows/ci.yml  # keyless cassette smoke test on push / PR
```

### Authoring options

The slices above exercise all four of AgentShip's authoring paths (documented in
the framework's [`examples/README.md`](../agentship/examples/README.md)):

- `template: single` — zero author code (slice 3, `assistant.yaml`).
- `template: graph` — a fillable multi-agent supervisor scaffold (slice 4).
- `template: deepagents` — a prebuilt autonomous deep-agent; **builds/compiles
  today, autonomous tool-using turn is Phase 03** (slice 5).
- **custom `build_graph`** — full control: subclass `LangGraphAgent` and write
  native LangGraph in `build_graph(model, tools)`, referenced from the spec's
  `code:` field. The harness wires the `model` and drives `run`/`stream`; you never
  wire a vendor (slice 6, `agents/custom/`).

> A **durable** multi-agent runtime (persistent members, richer routing,
> checkpoints) arrives at **Phase 02**. Slice 4 today is the authoring *scaffold* —
> a real routed coordinator → worker graph — and is labeled as such; it does not
> pretend to a durable runtime the framework hasn't shipped.

## Install

### Now (dev mode — editable-local)

AgentShip is not on PyPI yet, so install the framework editable from the sibling
monorepo checkout. This repo expects the framework at `../agentship`.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
# or, equivalently:
make install
```

### Later (pinned pip — once AgentShip is published)

The intended install for a real user, matching the pin in `pyproject.toml`.
`[starter]` is AgentShip's documented default stack (kernel + LangGraph engine + CLI):

```bash
pip install "agentship[starter]==0.0.1"
```

When that works, the editable-local step above (and the sibling-checkout step in
CI) go away.

## See every capability run (`make demo`)

```bash
make demo    # keyless; prints a labeled block per capability; exits non-zero on failure
```

No key needed — offline slices run live, real-model slices replay cassettes.

## Run one agent

The `echo` slices need no key at all:

```bash
agentship run agents/echo.yaml --input "hi"            # → echo: hi
agentship run agents/echo.yaml --input "hi" --stream   # → streamed: echo: hi
```

For a real-model slice, the CLI loads a `.env` from the current directory:

```bash
cp .env.example .env
# edit .env and set: OPENAI_API_KEY=sk-...

agentship run agents/assistant.yaml --input "Give one productivity tip."
# → e.g. "Batch similar tasks together to reduce context-switching."
```

`make run INPUT="..."` does the same for the assistant. Without a valid key a
real-model run prints a clean `Error: …` line (not a traceback) and exits non-zero.

## Test (no API key needed)

The whole suite is keyless — one test per capability. Offline slices run live; the
real-model slices replay a committed, redacted cassette that never touches the
network:

```bash
env -u OPENAI_API_KEY pytest -q
# or: make test
```

### Re-recording a cassette

Only the four real-model slices have a cassette (`test_streaming.py`,
`test_smoke.py`, `test_graph.py`, `test_custom.py`), and only when you change that
agent's prompt/model/input. With a real key present, record just that slice:

```bash
set -a; source ../agentship/.env; set +a      # a real OPENAI_API_KEY
pytest tests/test_streaming.py --record-mode=once  # or test_smoke.py / test_graph.py / test_custom.py
```

Then confirm it replays keyless and check for leaks before committing:

```bash
env -u OPENAI_API_KEY pytest -q
grep -rIiE 'sk-[A-Za-z0-9]{6}|Bearer [A-Za-z0-9]|AIza[0-9A-Za-z_-]{20}' tests/cassettes
# must print nothing
```
