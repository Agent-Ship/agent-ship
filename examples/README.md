# Examples

## `hello.yaml` — the walking skeleton

A single agent on the zero-dependency `echo` engine. No API key, no model — it
just echoes your input, proving the whole spine (author → build → run/stream →
CLI) works end to end.

```bash
agentship run examples/hello.yaml --input "hi"
# echo: hi

agentship run examples/hello.yaml --input "hi" --stream
# echo: hi
```

## `assistant.yaml` — a real LLM answer

A single agent on the default `langgraph` engine, backed by a real model via
LiteLLM (`openai/gpt-4o-mini`). Needs the engine's deps and a provider key:

```bash
pip install 'agentship[langgraph]'
export OPENAI_API_KEY=sk-...

agentship run examples/assistant.yaml --input "Name three primary colors."
# → e.g. "The three primary colors are red, blue, and yellow."
```

The offline test suite proves this wiring with an injected fake model (no
network); the live path is proven by a recorded, replayed cassette (no key needed
to replay).

Each later phase adds a runnable example here for the capability it ships.
