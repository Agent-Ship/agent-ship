# AgentShip demo

A small, **forkable** app built on [AgentShip](../agentship) that shows every
feature shipped so far, with one runnable slice per feature, plus a single
`make demo` that runs them all and prints a real result for each.

**This demo is LIVE.** It needs a real API key and it calls OpenAI for real, so it
costs a little each time you run it. There are no fakes here: no echo stand-in, no
saved recordings, no offline models. Every slice and every test makes a real call to
the OpenAI API. With a key, you can literally *see* every feature answer for real:
`make demo`.

New slices arrive one per phase as the framework ships each feature.

## Get set up

You need a real OpenAI API key. Copy the template and fill it in:

```bash
cp .env.example .env
# edit .env and set: OPENAI_API_KEY=sk-...
```

Then install the framework (editable from the sibling checkout until AgentShip is on
PyPI) into a virtual env:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt     # or: make install
```

## See every feature run for real

```bash
make demo
```

This calls OpenAI for real for every slice and prints each real result. The
streaming slice prints tokens as they arrive. If no `OPENAI_API_KEY` is set it prints
a clear message and stops — it never falls back to anything fake.

`make run INPUT="..."` runs just the single-agent slice for one real turn.

## What this demo shows

Five real slices. Each one is an agent (under `agents/`) plus a live test (under
`tests/`) that actually calls OpenAI. `make demo` runs all five top to bottom.

| # | Feature | Slice | What you see | Run its test (with a key set) |
|---|---|---|---|---|
| 1 | **`template: single`** — zero author code | `agents/assistant.yaml` | a real `gpt-4o-mini` answer | `pytest tests/test_smoke.py -q` |
| 2 | **Streaming** — real token-by-token | `agents/streaming.yaml` | the answer arrives as **more than one** real token chunk, reassembling into the full answer | `pytest tests/test_streaming.py -q` |
| 3 | **`template: graph`** — supervisor scaffold | `agents/graph.yaml` | coordinator routes -> worker answers, for real. **Scaffold only — durable multi-agent runtime = Phase 02.** | `pytest tests/test_graph.py -q` |
| 4 | **Custom `build_graph`** — native LangGraph | `agents/custom/` | the *author's* own graph answers, for real | `pytest tests/test_custom.py -q` |
| 5 | **`ModelRouter`** — pick then run | `tests/test_router.py` / `make demo` | the router picks the model id, then a **real turn** through that model answers. The default router is a simple pass-through today. | `pytest tests/test_router.py -q` |

Each row's last column runs only that slice's live test (needs a key). To run **all**
the live tests at once:

```bash
pytest -q      # or: make test
```

Honest labels are enforced, not decorative: `graph` is the authoring *scaffold* (the
durable multi-agent runtime lands in Phase 02); the default model router is a simple
pass-through today. Neither claims a feature that isn't shipped.

> **`deepagents` returns in Phase 03.** A real deepagents run needs tool execution,
> which lands in Phase 03, so there is no deepagents slice today. It comes back as a
> live slice then.

## What's here

```
agentship-demo/
  agents/
    assistant.yaml          # slice 1: template: single — one real-model assistant
    streaming.yaml          # slice 2: real gpt-4o-mini streamed token by token
    graph.yaml              # slice 3: template: graph — supervisor scaffold
    custom/
      custom.yaml           # slice 4: custom build_graph spec (code: reference)
      agent.py              # slice 4: the native LangGraph agent it points at
  demos/
    run_all.py              # `make demo` runner — runs every slice LIVE, prints each result
  tests/
    conftest.py             # the requires_live_key skip guard + LiteLLM transport setup
    test_smoke.py           # slice 1: single template real answer (live)
    test_streaming.py       # slice 2: real gpt-4o-mini multi-token stream (live)
    test_graph.py           # slice 3: graph scaffold routed answer (live)
    test_custom.py          # slice 4: custom graph answer (live)
    test_router.py          # slice 5: router picks the model, then a real turn runs (live)
  pyproject.toml            # pins agentship[starter]==0.0.1 (future PyPI install)
  requirements-dev.txt      # editable-local install of the framework (dev mode)
  Makefile                  # install / test / demo / run shortcuts
  .env.example              # credentials template — copy to .env and set OPENAI_API_KEY
  .github/workflows/ci.yml  # runs the live tests (needs the OPENAI_API_KEY repo secret)
```

### Authoring options

The slices above exercise AgentShip's authoring paths (documented in the framework's
[`examples/README.md`](../agentship/examples/README.md)):

- `template: single` — zero author code (slice 1, `assistant.yaml`).
- `template: graph` — a fillable multi-agent supervisor scaffold (slice 3).
- **custom `build_graph`** — full control: subclass `LangGraphAgent` and write native
  LangGraph in `build_graph(model, tools)`, referenced from the spec's `code:` field.
  The harness wires the `model` and drives `run`/`stream`; you never wire a vendor
  (slice 4, `agents/custom/`).

> A **durable** multi-agent runtime (persistent members, richer routing, checkpoints)
> arrives at **Phase 02**. Slice 3 today is the authoring *scaffold* — a real routed
> coordinator -> worker graph — and is labeled as such; it does not pretend to a
> durable runtime the framework hasn't shipped.

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

When that works, the editable-local step above (and the sibling-checkout step in CI)
go away.

## Run one agent

The CLI loads a `.env` from the current directory, so with your key set:

```bash
agentship run agents/assistant.yaml --input "Give one productivity tip."
# → e.g. "Batch similar tasks together to reduce context-switching."
```

`make run INPUT="..."` does the same. Without a valid key, a run prints a clean
`Error: …` line (not a traceback) and exits non-zero.

## Test (live — needs a key)

Every test makes a real call to OpenAI. With a key set they run for real; without a
key they skip cleanly (they never fake-pass and never hard-error). The tests use
short prompts and small answers to keep each run fast and cheap.

```bash
set -a; source ../agentship/.env; set +a     # a real OPENAI_API_KEY
pytest -q                                     # or: make test
```

To run one slice's test:

```bash
pytest tests/test_streaming.py -q
```
