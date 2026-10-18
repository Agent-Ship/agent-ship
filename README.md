<p align="center">
  <img src="branding/banner/github-readme-banner@2x.png" alt="AgentShip — production plumbing for AI agents, without the rewrite" width="100%">
</p>

<p align="center">
  Author an agent — or a team of agents — in YAML or Python.<br>
  Get the production stack wired for you: tools, memory, tracing, streaming, and a served API.
</p>

<p align="center">
  <!--
    Ours are violet, so the accent still reads as "AgentShip"; the stack badges keep each
    tool's own recognisable colour. Every badge shares labelColor=0D1317 (Ink), which is what
    holds the row together. Six identical accent pills was the previous version, and it spent
    the accent on everything, which is the same as spending it on nothing.
  -->
  <a href="https://pypi.org/project/agentship-sdk/"><img src="https://img.shields.io/pypi/v/agentship-sdk?label=PyPI&color=A685EE&labelColor=0D1317&logo=pypi&logoColor=E8EEF0" alt="PyPI"></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/Python-3.13%2B-3776AB?labelColor=0D1317&logo=python&logoColor=E8EEF0" alt="Python 3.13+"></a>
  <a href="https://www.langchain.com/langgraph"><img src="https://img.shields.io/badge/LangGraph-engine-1C3C3C?labelColor=0D1317&logo=langchain&logoColor=E8EEF0" alt="LangGraph"></a>
  <a href="https://modelcontextprotocol.io/"><img src="https://img.shields.io/badge/MCP-tools-D97757?labelColor=0D1317&logo=modelcontextprotocol&logoColor=E8EEF0" alt="MCP"></a>
  <a href="https://opentelemetry.io/"><img src="https://img.shields.io/badge/OpenTelemetry-traces-425CC7?labelColor=0D1317&logo=opentelemetry&logoColor=E8EEF0" alt="OpenTelemetry"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-Apache%202.0-A685EE?labelColor=0D1317" alt="License: Apache 2.0"></a>
</p>

<p align="center">
  <a href="https://agent-ship.dev"><b>agent-ship.dev</b></a> ·
  <a href="docs">Docs</a> ·
  <a href="examples">Examples</a> ·
  <a href="deploy/RAILWAY.md">Deploy</a> ·
  <a href="postman">Postman</a>
</p>

---

## The problem

Your agent works in a notebook. Shipping it means a REST API, session storage, tracing that
reports real token costs, streaming with its error cases, and packaging. That is a couple of
thousand lines of infrastructure with nothing to do with what your agent *does* — and you
rebuild it for the next one.

The usual answer is a framework that owns everything. Then you are inside it, and the day
you need something it lacks, you are rewriting.

> **AgentShip integrates best-of-breed libraries behind small, stable seams.**
> LangGraph runs the graph. LiteLLM talks to models. MCP supplies tools. OpenTelemetry
> carries traces. We reimplement none of them — we wire them together and add the plumbing
> none of them ship.

---

## Quick start

```bash
pip install "agentship-sdk[starter,observability]"
```

One agent, one tool:

```yaml
# calc.yaml
name: calculator-agent
engine: langgraph
model: openai/gpt-4o-mini
prompt: Use the calculator for arithmetic. Answer with just the number.
tools: [calculator]
```

```bash
agentship run calc.yaml --input "What is 17 times 23?"
# 391
```

A team, with a coordinator that routes on each member's `description`:

```yaml
# team.yaml
name: research-team
engine: langgraph
model: openai/gpt-4o-mini
members:
  - name: analyst
    description: arithmetic, comparisons, and calculations over known numbers
    model: openai/gpt-4o-mini
    prompt: Answer with the number and one short sentence.
  - name: writer
    description: explanations, summaries, and anything needing prose
    model: anthropic/claude-sonnet-4-6
    prompt: Answer in two clear sentences.
```

A member can be inline like this, a `ref:` to its own YAML file, or a networked agent
reached over A2A. Each runs as its own turn, nested inside the coordinator's trace.

```bash
agentship serve --agents-dir .     # /v1 REST + SSE + WebSocket, and Studio at /studio
```

**No API key?** The `echo` engine runs offline, so the transports, auth, error model and
Studio are all explorable for free:

```yaml
name: hello
engine: echo
prompt: A stand-in agent that needs no provider.
```

---

## How it fits together

One YAML spec is compiled by an **engine adapter** into somebody else's graph, and run through
a kernel that imports no vendor library. Everything below is in the tree today — dashed boxes
are seams with a contract and one implementation, not roadmap.

```mermaid
%%{init: {"theme":"base","themeVariables":{
  "primaryColor":"#141C22","primaryTextColor":"#E8EEF0","primaryBorderColor":"#2A3944",
  "lineColor":"#55656E","textColor":"#C9D5DB",
  "clusterBkg":"#0D1317","clusterBorder":"#243039",
  "edgeLabelBackground":"#141C22","tertiaryColor":"#141C22",
  "fontFamily":"ui-sans-serif, system-ui, -apple-system, sans-serif","fontSize":"14px"
}}}%%
flowchart TB
  %% Brand palette. Every node is Panel (#141C22) with Fog text; only the STROKE carries
  %% meaning, so the accent is spent once — on the kernel, which is what the diagram argues
  %% for. Previously five pastel fills (amber, mint, green, slate, blue) rendered this as a
  %% bright light-mode block in the middle of a dark-branded README.
  classDef spec    fill:#141C22,stroke:#34454F,stroke-width:1.5px,color:#E8EEF0
  classDef system  fill:#141C22,stroke:#A685EE,stroke-width:2px,color:#E8EEF0
  classDef adapter fill:#141C22,stroke:#5CCFC0,stroke-width:1.5px,color:#E8EEF0
  classDef ext     fill:#141C22,stroke:#34454F,stroke-width:1px,color:#9FB0B9
  classDef obs     fill:#141C22,stroke:#34454F,stroke-width:1.5px,color:#C9D5DB

  SPEC["<b>agent.yaml</b><br/><i>[AgentSpec]</i><br/>name · engine · prompt<br/>tools · members"]:::spec

  CLI["<b>agentship-cli</b><br/><i>[Entry point]</i><br/>run · serve · doctor · verify"]:::spec
  SVC["<b>agentship-service</b><br/><i>[FastAPI]</i><br/>:invoke :resume :stream<br/>WS /live · /a2a · Studio"]:::spec

  subgraph CORE["agentship-core · imports no vendor"]
    RT["<b>RunnableAgent</b><br/><i>[Runtime]</i><br/>one turn: run / stream / resume"]:::system
    SEAM["<b>Seams</b><br/><i>[Contracts]</i><br/>auth · tenancy · tools<br/>observability · A2A"]:::system
    CONF["<b>Conformance matrix</b><br/><i>[Build gate]</i><br/>fails on a capability<br/>declared but not held"]:::system
  end

  subgraph ENG["engine adapters"]
    ECHO["<b>echo</b><br/><i>[Engine]</i><br/>no provider, no network"]:::adapter
    LG["<b>langgraph</b><br/><i>[Engine]</i><br/>single · graph · autonomous"]:::adapter
  end

  subgraph EXT["consumed, never reimplemented"]
    X1["<b>LangGraph</b><br/><i>[External]</i><br/>StateGraph · checkpointer"]:::ext
    X2["<b>LiteLLM</b><br/><i>[External]</i><br/>every model provider"]:::ext
    X3["<b>MCP adapters</b><br/><i>[External]</i><br/>stdio · http · OAuth"]:::ext
  end

  OBS["<b>agentship-observability</b><br/><i>[OTel exporter]</i><br/>Phoenix / Langfuse / Opik"]:::obs

  SPEC -->|compiled by| CLI
  SPEC -->|served by| SVC
  CLI --> CORE
  SVC --> CORE
  CORE -->|delegates the turn to| ENG
  CONF -.->|verifies| ENG
  LG --> X1
  LG --> X2
  LG --> X3
  CORE -->|emits spans| OBS

  %% The three containers, matching their contents: accent for the kernel, teal for the
  %% adapters, dim for what we consume and never reimplement.
  style CORE fill:#0D1317,stroke:#A685EE,stroke-width:1px
  style ENG  fill:#0D1317,stroke:#5CCFC0,stroke-width:1px
  style EXT  fill:#0D1317,stroke:#243039,stroke-width:1px
```

**Read it in one line:** your spec never names a vendor, the kernel never imports one, and the
adapter is the single place that does — so replacing LangGraph is one package, not a rewrite.

`echo` is the proof rather than a toy: it is a second engine with no provider, no network and
no key, which is why the conformance matrix can tell a capability an engine *declares* from one
it actually *has*.

**Not in this picture, because it is not built yet:** long-term memory, guardrails/PII,
sandboxing, evals, and the ADK / Pydantic AI adapters. See [Status](#status).

---

## What you get

**Multi-agent.** A coordinator classifies the request against each member's `description`,
dispatches, and merges the result. Members nest properly in the trace — a sub-agent's model
and tool calls appear beneath it, not in a separate trace.

**Tools and MCP.** Built-in tools (`calculator`, `web_search`, `scrape_url`), your own
Python functions, and any MCP server — stdio or streamable HTTP — through
`langchain-mcp-adapters`. An MCP tool and a native one are indistinguishable to the graph.

**Memory and durability.** Short-term conversation memory by default; PostgreSQL
checkpoints when a run needs to survive a restart. A run that pauses for a human returns a
resume token, and `POST /v1/agents/{name}:resume` continues it.

**Observability that shows the money.** One OpenTelemetry span tree per turn:

```
agent triage
  node.classify
    chat openai/gpt-4o-mini      in=43  out=1   $0.000007
  agent billing_specialist
    chat openai/gpt-4o-mini      in=43  out=54  $0.000039
```

Spans are named after the thing that ran — `agent <name>`, `chat <model>` — rather than
`agent` and `model`, so a backend's list is readable. Turns carry `thread_id`, so a
conversation groups into one thread instead of scattering into unrelated traces. Exports to
Opik, LangFuse, LangSmith or Phoenix: set `AGENTSHIP_OTEL_EXPORTERS` and the credentials,
and nothing in your spec changes.

**Reasoning models.** `reasoning_effort: minimal | low | medium | high` — one knob, mapped
by LiteLLM onto whichever scale the provider uses. Thinking arrives on its own `reasoning`
stream frame, never mixed into the answer, and reasoning tokens are recorded separately so
a turn that cost 5× shows why.

**Serving.** `/v1` REST, SSE streaming, WebSocket, RFC-9457 problem documents, API-key or
JWT auth, per-tenant isolation, and validated structured output when a spec declares an
`output_schema`.

**Studio, built in.** A chat and trace UI at `/studio`, served by the same process — one
self-contained HTML file, no build step and no CDN. It shows what the agent is doing while
it works, rather than a spinner:

```
✓ Thinking…
✓ Calling web_search…
✓ Reading web_search result…
  the answer
```

---

## Packages

Several distributions, released together, one version. Install only what you need.

| Package | What it is |
|---|---|
| [`agentship-sdk`](https://pypi.org/project/agentship-sdk/) | The batteries-included meta-package |
| [`agentship-core`](https://pypi.org/project/agentship-core/) | The vendor-free kernel: spec, runtime, registry, middleware, `echo` engine |
| [`agentship-langgraph`](https://pypi.org/project/agentship-langgraph/) | The reference engine — compiles a spec into a LangGraph `StateGraph` |
| [`agentship-service`](https://pypi.org/project/agentship-service/) | The FastAPI app behind `/v1`, plus Studio |
| [`agentship-observability`](https://pypi.org/project/agentship-observability/) | The OpenTelemetry pipeline and its exporters |
| [`agentship-cli`](https://pypi.org/project/agentship-cli/) | `agentship run`, `serve`, `verify`, `doctor`, `init` |
| `agentship-voice` | The same agent over a live audio stream (Pipecat or LiveKit); first published with `0.0.3` |

`agentship-core` imports no vendor library. That is what keeps the seams honest: the engine
can be replaced without touching the kernel, and a conformance matrix fails the build if an
engine declares a capability it does not actually have.

```bash
pip install "agentship-sdk[starter,observability]"   # the usual stack
pip install "agentship-sdk[all]"                     # everything
pip install "agentship-langgraph[mcp]"               # MCP tools
pip install "agentship-core[postgres]"               # durable checkpoints
```

---

## Commands

```bash
agentship init my-project              # scaffold a project
agentship new-agent support            # scaffold one agent from a template
agentship run agent.yaml               # one turn, printed
agentship serve --agents-dir agents    # the /v1 API + Studio
agentship doctor                       # validate every spec against its engine
agentship verify                       # prove engines honour what they declare
agentship db upgrade                   # apply checkpoint migrations (gated)
```

`doctor` and `verify` exist because a capability an engine *declares* and one it actually
*has* are different things. `verify` fails the build when they diverge.

---

## Reference

| | |
|---|---|
| Capability guides | [`docs/capabilities/`](docs/capabilities/) — [multi-agent](docs/capabilities/multi-agent.md) · [tools & MCP](docs/capabilities/tools-and-mcp.md) · [observability](docs/capabilities/observability.md) · [service & security](docs/capabilities/service-and-security.md) · [checkpointing & HITL](docs/capabilities/checkpointing-and-hitl.md) · [durable resume](docs/capabilities/durable-resume.md) |
| Architecture decisions | [`docs/decisions/`](docs/decisions/) — why we integrate rather than reimplement |
| Runnable examples | [`examples/`](examples/) |
| Releasing and versioning | [`docs/RELEASING.md`](docs/RELEASING.md) |
| Deploying | [`deploy/RAILWAY.md`](deploy/RAILWAY.md) |
| API collection | [`postman/`](postman/) |
| Contributing | [`CONTRIBUTING.md`](CONTRIBUTING.md) |
| Changelog | [`docs/CHANGELOG.md`](docs/CHANGELOG.md) |

---

## Status

**Early — `0.x`, so the API can change between minor versions.** Pin exactly if that matters
to you (`agentship-sdk==0.0.4`, the latest on PyPI). See
[RELEASING](docs/RELEASING.md#versioning).

> **`0.0.1` is yanked.** It reserved the six names on PyPI but could not run an agent —
> tracing was on by default and the adapter is not in `[starter]`, so every run failed with
> `observability provider 'otel' is not installed`. Fixed in **0.0.2**; every resolver skips
> `0.0.1`, so a plain `pip install` gets the working one.

**What is built** is the diagram above: the spec, the kernel and its seams, two engines, the
`/v1` service, the CLI, OpenTelemetry tracing, and voice. **What is not built yet:** long-term
memory, guardrails/PII, sandboxing, evals, and the ADK / Pydantic AI adapters. The JOSS
figures in [`figures/`](figures/) show the full intended system, not today's tree.

Rebuilt foundation-first: one thin working slice per phase, with tests and a runnable demo
before anything is called done. Test first, one task per commit, CI green — see
[CONTRIBUTING](CONTRIBUTING.md).

## Licence

[Apache 2.0](LICENSE).

The fonts vendored under [`branding/fonts/`](branding/fonts/) and `docs/assets/fonts/` are
redistributed under the SIL Open Font License 1.1, with each licence kept beside them. The
AgentShip name and logo are project trademarks, which Apache 2.0 does not grant rights to — see
[`branding/README.md`](branding/README.md#licensing).
