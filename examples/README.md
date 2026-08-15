# Examples

## The four authoring paths

AgentShip gives you four ways to author an agent, trading zero code for full
control. Each has a runnable example here:

| Path | One-liner | Example | Run |
|---|---|---|---|
| `template: single` | Zero author code — a prebuilt ReAct agent from the YAML alone. | `quickstart.yaml` | `agentship run examples/quickstart.yaml --input "..."` |
| `template: graph` | A fillable multi-agent supervisor scaffold (coordinator → worker) with `# TODO(author)` seams. | `graph.yaml` | `agentship run examples/graph.yaml --input "..."` |
| `template: autonomous` | A single self-directing agent that plans and calls its own tools (deepagents library). | `autonomous.yaml` | `agentship run examples/autonomous.yaml --input "..."` |
| custom `build_graph` via `code:` | Full control — subclass `LangGraphAgent` and write native LangGraph; the harness wires `model`/`tools`. | `custom/agent.py` + `custom/custom.yaml` | `agentship run examples/custom/custom.yaml --input "..."` |

Each template needs no Python; the custom path is the only one you write code for.
All four are proven offline (fake model) by
`packages/agentship-langgraph/tests/test_authoring_examples.py`. The sections below
detail each, plus provider swapping and generation tuning.

## Testing the examples

Every example on this page is backed by a test, and **all of them run with no API
key** — offline examples run against a fake model, and the live examples are proven
by *replayed, redacted VCR cassettes* (the real HTTP round-trip is committed, so
replay never touches the network or needs a key).

**Run all example tests, keyless, in one go:**

```bash
env -u OPENAI_API_KEY -u ANTHROPIC_API_KEY -u GEMINI_API_KEY -u GOOGLE_API_KEY \
  python -m pytest \
  packages/agentship-cli/tests/test_cli.py \
  packages/agentship-langgraph/tests/test_authoring_examples.py \
  packages/agentship-langgraph/tests/test_assistant_example.py \
  packages/agentship-langgraph/tests/test_streaming_live.py \
  packages/agentship-langgraph/tests/test_provider_examples.py \
  packages/agentship-langgraph/tests/test_tuning_examples.py -q
```

**Run just ONE example's test** — target its test node id with the `::` pattern.
Every example below carries its own **Test it (no key):** line with the exact
command, e.g.:

```bash
env -u OPENAI_API_KEY python -m pytest \
  packages/agentship-langgraph/tests/test_authoring_examples.py::test_quickstart_single_template_example_runs -q
```

The `-u OPENAI_API_KEY` unsets the key so the command *proves* it runs keyless; add
`-u ANTHROPIC_API_KEY -u GEMINI_API_KEY -u GOOGLE_API_KEY` for the provider matrix.
The two provider *live* cassettes that are not yet recorded (**anthropic** and
**gemini**) are **skipped**, not failed — only openai has a committed cassette so
far.

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

**Test it (no key):**

```bash
# echo run
env -u OPENAI_API_KEY python -m pytest \
  packages/agentship-cli/tests/test_cli.py::test_cli_run_prints_echo_output -q
# echo --stream
env -u OPENAI_API_KEY python -m pytest \
  packages/agentship-cli/tests/test_cli.py::test_cli_stream_prints_echo_output -q
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

`run` returns the model's **whole** answer as one result. `--stream` streams the
answer as **real tokens** from the provider — the model emits many small chunks
(e.g. `Merc` · `ury` · `,` · …) that reassemble into the full text:

```bash
agentship run examples/assistant.yaml --input "Name the 8 planets." --stream
# → Mercury, Venus, Earth, Mars, Jupiter, Saturn, Uranus, Neptune.  (arrives token by token)
```

The offline test suite proves this wiring with an injected fake model (no
network). Two live paths are proven by recorded, replayed cassettes (no key needed
to replay): `run` returns the full answer
(`tests/test_live_model.py`), and `--stream` yields **multiple real token chunks**
(`tests/test_streaming_live.py` asserts more than one `content` event, reassembling
to the full answer). Real token streaming requires the chat model to be built with
`streaming=True` (see `resolve_model`); without it a real provider returns one whole
message and `--stream` would yield nothing — the engine also carries a fallback that
emits the whole answer as a single `content` event if a model ever refuses to stream,
so `--stream` never silently produces zero output.

The saved-recording test above shows streaming comes out in many pieces, but a
recording plays back instantly, so it can't show the pieces really arrived one at a
time. To check that — that tokens genuinely stream in over time, and we didn't
quietly slip back to "wait for the whole answer, then hand it out in pieces" — there
is a separate test that calls the real OpenAI API and confirms the pieces arrive
spread out over time (`tests/test_streaming_sends_tokens_as_they_arrive.py`, tagged
`live`). It needs a real key, so it stays out of the normal no-key tests (it skips
itself without `OPENAI_API_KEY`) and runs in the daily real-API job
(`.github/workflows/daily-real-api-tests.yml`):

```bash
set -a; source .env; set +a   # real OPENAI_API_KEY
python -m pytest -m live -q
```

**Test it (no key):**

```bash
# offline (injected fake model, no network)
env -u OPENAI_API_KEY python -m pytest \
  packages/agentship-langgraph/tests/test_assistant_example.py::test_assistant_example_runs_offline_with_fake_model -q
# LIVE run, replayed redacted cassette
env -u OPENAI_API_KEY python -m pytest \
  packages/agentship-langgraph/tests/test_assistant_example.py::test_assistant_example_runs_live_via_cassette -q
# LIVE real token streaming (multiple token chunks), replayed cassette
env -u OPENAI_API_KEY python -m pytest \
  packages/agentship-langgraph/tests/test_streaming_live.py::test_real_gpt_4o_mini_streams_multiple_token_chunks -q
```

## `quickstart.yaml` — the `single` template (zero author code)

A `template: single` agent: the whole build body is generated by LangGraph's
prebuilt ReAct agent, so a runnable agent comes from the YAML *alone* — no Python.

```yaml
name: quickstart
engine: langgraph
template: single
model: openai/gpt-4o-mini
prompt: You are a concise assistant. Answer in one short sentence.
```

```bash
export OPENAI_API_KEY=sk-...
agentship run examples/quickstart.yaml --input "Name three primary colors."
```

The offline test proves it builds and runs with an injected fake model (no
network) — and that the build actually dispatches through `create_react_agent`,
not the engine's default single-node graph.

**Test it (no key):**

```bash
env -u OPENAI_API_KEY python -m pytest \
  packages/agentship-langgraph/tests/test_authoring_examples.py::test_quickstart_single_template_example_runs -q
```

## `graph.yaml` — the `graph` template (supervisor scaffold)

A `template: graph` agent: a fillable multi-agent starting point. The generated
build body is a real, compilable supervisor `StateGraph` that routes a
**coordinator** to one **worker**, with `# TODO(author)` markers where you add
specialists, tools, and richer routing (in
`agentship_langgraph/templates/graph.py`). This is the authoring scaffold only — a
full *durable* multi-agent runtime is Phase 02.

```yaml
name: triage
engine: langgraph
template: graph
model: openai/gpt-4o-mini
prompt: Route the user request to the right specialist, then answer.
```

```bash
export OPENAI_API_KEY=sk-...
agentship run examples/graph.yaml --input "Help me plan a trip."
```

The offline test builds the scaffold with a fake model and drives a turn end to
end (coordinator routes → worker answers), proving the scaffold is a real routed
graph, not a single node.

**Test it (no key):**

```bash
env -u OPENAI_API_KEY python -m pytest \
  packages/agentship-langgraph/tests/test_authoring_examples.py::test_graph_template_example_runs -q
```

## `autonomous.yaml` — the `autonomous` template (single self-directing agent)

A `template: autonomous` agent: the build body is the deepagents library's
prebuilt autonomous agent (`create_deep_agent`) over the wired model, using
`spec.prompt` as its system prompt — zero author code. This is *one* agent that
plans and calls its own tools in a loop (not the multi-agent supervisor — that is
`template: graph`). A full autonomous turn uses tools (tool execution lands in
Phase 03); the template *builds and compiles* today.

```yaml
name: researcher
engine: langgraph
template: autonomous
model: openai/gpt-4o-mini
prompt: You are an autonomous research agent. ...
```

```bash
pip install 'agentship[langgraph]'   # deepagents ships with the langgraph extra
export OPENAI_API_KEY=sk-...
agentship run examples/autonomous.yaml --input "Research the fastest land animal."
```

The offline test builds this exact file with a fake model and asserts it compiles
into an autonomous-agent graph (not the engine's default single node). `agentship
doctor` pins the supported deepagents version, since the library is pre-1.0.

**Test it (no key):**

```bash
env -u OPENAI_API_KEY python -m pytest \
  packages/agentship-langgraph/tests/test_authoring_examples.py::test_autonomous_template_example_builds -q
```

## `custom/` — a custom LangGraph agent (native authoring)

Full control: subclass `LangGraphAgent` and write native LangGraph in
`build_graph(model, tools)`. The harness wires the `model`, drives
`run`/`stream`, and manages the run context; you never wire a vendor and never
lose native power (nodes, edges, routing, subgraphs, `interrupt()`).

- `custom/agent.py` — an `EchoingAssistant(LangGraphAgent)` whose `build_graph`
  builds a real `StateGraph` over the wired model.
- `custom/custom.yaml` — a spec whose `code:` points at that agent's factory.

```yaml
name: custom-assistant
engine: langgraph
code: examples/custom/agent.py:build_agent
```

```bash
export OPENAI_API_KEY=sk-...
agentship run examples/custom/custom.yaml --input "Name three primary colors."
```

The offline test builds and runs the exact file with a fake model injected, and
asserts it is the *author's* graph node that executes (not the default build
body) — proving custom authoring end to end.

**Test it (no key):**

```bash
env -u OPENAI_API_KEY python -m pytest \
  packages/agentship-langgraph/tests/test_authoring_examples.py::test_custom_build_graph_example_runs -q
```

## `tuned.yaml` — tune generation

The same `langgraph` engine, plus a `params:` block that tunes how the model
generates. Every field is optional; an omitted one keeps the model's default.

```yaml
params:
  temperature: 0.2   # lower = more deterministic
  max_tokens: 256    # cap the answer length
```

```bash
export OPENAI_API_KEY=sk-...
agentship run examples/tuned.yaml --input "Name three primary colors."
```

`params:` also accepts `api_base` (see below) and `timeout` (per-request seconds).
An unknown param key or a bad type (e.g. `temperature: hot`) fails loudly at load
time — it is never silently dropped.

**Test it (no key):**

```bash
env -u OPENAI_API_KEY python -m pytest \
  packages/agentship-langgraph/tests/test_tuning_examples.py::test_tuned_example_threads_generation_params -q
```

## Local & self-hosted models

Because the model is resolved through LiteLLM, the same agent runs against a
**local / self-hosted** OpenAI-compatible server (Ollama, vLLM, LM Studio, …) by
setting the `model:` id and pointing `params.api_base` at the server. **No API key
is needed** for a local model — the credential check never fires for a local run.

`examples/local.yaml` targets a local [Ollama](https://ollama.com):

```yaml
model: ollama/llama3
params:
  api_base: "http://localhost:11434"
```

Run it against a real Ollama (not exercised in CI — it needs a running server):

```bash
ollama serve            # start the local server
ollama pull llama3      # fetch the model once
agentship run examples/local.yaml --input "Say hello in one word."
```

If the server is not running, the run fails with a clean
`Error: Model call failed for 'ollama/llama3': ...` — a connection error, not a
raw traceback, and not a demand for `OPENAI_API_KEY`. The offline test suite
proves the `api_base` threads to the model call (no server, no key); the live path
is the documented manual run above.

**Test it (no key):**

```bash
# api_base threads to the model call
env -u OPENAI_API_KEY python -m pytest \
  packages/agentship-langgraph/tests/test_tuning_examples.py::test_local_example_threads_api_base -q
# a local model needs no OpenAI key
env -u OPENAI_API_KEY python -m pytest \
  packages/agentship-langgraph/tests/test_tuning_examples.py::test_local_example_needs_no_openai_key -q
```

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

**Test it (no key):**

```bash
# every provider example, offline against a fake model (all providers)
env -u OPENAI_API_KEY -u ANTHROPIC_API_KEY -u GEMINI_API_KEY -u GOOGLE_API_KEY python -m pytest \
  packages/agentship-langgraph/tests/test_provider_examples.py::test_provider_example_runs_offline_with_fake_model -q
# just one provider offline (e.g. openai)
env -u OPENAI_API_KEY -u ANTHROPIC_API_KEY -u GEMINI_API_KEY -u GOOGLE_API_KEY python -m pytest \
  packages/agentship-langgraph/tests/test_provider_examples.py::test_provider_example_runs_offline_with_fake_model -k "openai and offline" -q
# LIVE openai, replayed redacted cassette
env -u OPENAI_API_KEY -u ANTHROPIC_API_KEY -u GEMINI_API_KEY -u GOOGLE_API_KEY python -m pytest \
  packages/agentship-langgraph/tests/test_provider_examples.py::test_provider_example_runs_live_via_cassette -k openai -q
```

Only **openai** has a committed live cassette today. The `anthropic` and `gemini`
live replays are **skipped** (not failed) until their cassettes are recorded — run
the live node without `-k openai` and you'll see them reported as `SKIPPED`.

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
