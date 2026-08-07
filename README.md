# AgentShip demo

A small, **forkable** app built on [AgentShip](../agentship) — a single real-model
agent authored in YAML and run through the framework's CLI.

Its job is to prove the *published* framework actually works end to end: fork it,
install AgentShip by version, drop in a key, and run a real agent. The keyless
smoke test (below) is the anti-rot guard — it replays a recorded model round-trip
in CI so we catch packaging breakage (missing entry point, unwired extra, an import
that only resolves from the source tree) the moment it happens.

## What's here

```
agentship-demo/
  agents/
    assistant.yaml          # the demo app: one real-model research assistant
  tests/
    conftest.py             # VCR config — redacts credentials, replays keyless
    test_smoke.py           # loads the agent, runs it, asserts a non-empty answer
    cassettes/              # committed, redacted HTTP recordings (replayed in CI)
  pyproject.toml            # pins agentship[starter]==0.0.1 (future PyPI install)
  requirements-dev.txt      # editable-local install of the framework (dev mode)
  Makefile                  # install / test / run shortcuts
  .env.example              # credentials template — copy to .env and fill in
  .github/workflows/ci.yml  # keyless cassette smoke test on push / PR
```

The agent itself is four lines of YAML:

```yaml
name: assistant
engine: langgraph
model: openai/gpt-4o-mini
prompt: >-
  You are a concise research assistant. ...
```

> A multi-agent **team** demo (a supervisor routing work between members) arrives
> when the framework reaches **Phase 6**. Today the framework ships a single
> real-model agent, and this demo uses exactly that — nothing it can't run.

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

## Run the agent

The CLI loads a `.env` from the current directory, so put your key there:

```bash
cp .env.example .env
# edit .env and set: OPENAI_API_KEY=sk-...

agentship run agents/assistant.yaml --input "Give one productivity tip."
# → e.g. "Batch similar tasks together to reduce context-switching."
```

`make run INPUT="..."` does the same. Without a valid key the CLI prints a clean
`Error: …` line (not a traceback) and exits non-zero.

## Test (no API key needed)

The smoke test replays a committed, redacted cassette — it never calls the network
and needs no key:

```bash
env -u OPENAI_API_KEY pytest -q
# or: make test
```

### Re-recording the cassette

Only needed if you change the agent's prompt/model/input. With a real key present:

```bash
set -a; source ../agentship/.env; set +a      # a real OPENAI_API_KEY
pytest tests/test_smoke.py --record-mode=once
```

Then confirm it replays keyless and check for leaks before committing:

```bash
env -u OPENAI_API_KEY pytest -q
grep -rIiE 'sk-[A-Za-z0-9]|Bearer ' tests/cassettes   # must print nothing
```
