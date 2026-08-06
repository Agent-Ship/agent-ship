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

## `providers/` — swap any provider

Because the model is resolved through LiteLLM, the same agent runs against any
provider by changing one line (the `model:` id) and setting that provider's key.
`examples/providers/` ships one single-agent example per provider:

| Example | `model:` id | Env var | Live cassette |
|---|---|---|---|
| `providers/openai.yaml` | `openai/gpt-4o-mini` | `OPENAI_API_KEY` | recorded ✓ |
| `providers/anthropic.yaml` | `anthropic/claude-3-5-haiku-latest` | `ANTHROPIC_API_KEY` | pending — needs a real key to record |
| `providers/gemini.yaml` | `gemini/gemini-2.0-flash-lite` | `GEMINI_API_KEY` | pending — key had no free-tier quota to record |

```bash
export ANTHROPIC_API_KEY=sk-ant-...
agentship run examples/providers/anthropic.yaml --input "ping"
# → e.g. "pong"
```

The matrix these examples mirror lives in `tests/providers.py`
(`LIVE_PROVIDERS`). The offline suite runs every provider example with a fake
model (no key); each provider that has a recorded cassette also replays its real
round-trip keyless in CI. Providers with a key we don't have (e.g. `groq`,
`mistral`) are listed there commented as "add a key to record" — never silently
dropped.

### How to add a provider

1. **Append it to the matrix** — add one `Provider(name, model, env_var)` to
   `LIVE_PROVIDERS` in `tests/providers.py` (a cheap current model id).
2. **Add an example** — create `examples/providers/<name>.yaml` mirroring the
   others (`engine: langgraph`, the new `model:` id).
3. **Record its cassette once** — with a real key present:
   ```bash
   set -a; source /path/to/keys.env; set +a
   pytest tests/test_providers.py --record-mode=once
   ```
   This writes `tests/cassettes/test_providers/<name>.yaml` with the credential
   redacted (header **and** URL `key=` query param).
4. **Verify no secret leaked**, then commit the cassette:
   ```bash
   grep -rIiE 'sk-|Bearer |x-api-key: [A-Za-z0-9]|AIza[0-9A-Za-z_-]{20}|key=AIza' tests/cassettes
   # must print nothing
   ```
   CI now replays the new provider with no key.

Each later phase adds a runnable example here for the capability it ships.
