<p align="center">
  <img src="branding/banners/github-readme-banner@3x.png" alt="AgentShip – production plumbing for AI agents, without the rewrite" width="100%">
</p>

<p align="center">
  Author an agent — or a team of agents — in YAML or Python.<br>
  Get the production stack wired for you: tools, memory, tracing, streaming, and a served API.
</p>

<p align="center">
  <a href="https://pypi.org/project/agentship-sdk/"><img src="https://img.shields.io/pypi/v/agentship-sdk?style=flat&color=3776AB" alt="PyPI"></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/Python-3.13+-3776AB?style=flat&logo=python&logoColor=white" alt="Python 3.13+"></a>
  <a href="https://www.langchain.com/langgraph"><img src="https://img.shields.io/badge/LangGraph-engine-121212?style=flat&logo=langchain&logoColor=white" alt="LangGraph"></a>
  <a href="https://modelcontextprotocol.io/"><img src="https://img.shields.io/badge/MCP-tools-FF6B35?style=flat" alt="MCP"></a>
  <a href="https://opentelemetry.io/"><img src="https://img.shields.io/badge/OpenTelemetry-tracing-425CC7?style=flat&logo=opentelemetry&logoColor=white" alt="OpenTelemetry"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-Apache_2.0-yellow.svg" alt="License: Apache 2.0"></a>
</p>

---

## The problem

Your agent works in a notebook. Shipping it means building a REST API, wiring session
storage, adding tracing that reports real token costs, handling streaming and its error
cases, and packaging the lot. That is a couple of thousand lines of infrastructure with
nothing to do with what your agent actually *does* — and you rebuild it for the next one.

The usual answer is a framework that owns everything, and then you are inside it. When you
need one capability it lacks, you are rewriting.

> **AgentShip integrates best-of-breed libraries behind small, stable seams.**
> LangGraph runs the graph. LiteLLM talks to models. MCP supplies tools. OpenTelemetry
> carries traces. We do not reimplement any of them — we wire them together and give you
> the plumbing none of them ship.

<p align="center">
  <img src="branding/hero/image.png" alt="AgentShip architecture" width="100%">
</p>

---

## Quick start

```bash
pip install "agentship-sdk[starter]"
```

```yaml
# team.yaml — a supervisor with two specialists
name: research-team
engine: langgraph
template: supervisor
members:
  - name: researcher
    model: openai/gpt-4o-mini
    prompt: Find and summarise the facts.
    tools: [web_search]
  - name: writer
    model: anthropic/claude-sonnet-4-6
    prompt: Write the final answer from the research.
```

```bash
agentship run team.yaml --input "What changed in EU AI regulation this year?"
agentship serve --agents-dir .        # REST + SSE + WebSocket, and Studio at /studio
```

No key to hand? The `echo` engine runs offline, so the whole surface is explorable for free:

```yaml
name: hello
engine: echo
prompt: A stand-in agent that needs no provider.
```

---

## What you get

| | |
|---|---|
| **Multi-agent** | A supervisor routes to specialists and merges their answers. Sub-agents nest properly in traces. |
| **Tools & MCP** | Built-in tools plus any MCP server — stdio or streamable HTTP — via `langchain-mcp-adapters`. |
| **Memory** | Short-term conversation memory, and durable checkpoints in PostgreSQL so a run survives a restart. |
| **Human-in-the-loop** | A run can pause on an `interrupt`, hand back a resume token, and continue when a human decides. |
| **Observability** | One OpenTelemetry span tree per turn — agent, model, tool and MCP spans, with tokens and cost — exported to Opik, LangFuse, LangSmith or Phoenix. |
| **Serving** | `/v1` REST, SSE streaming, WebSocket, RFC-9457 errors, API-key or JWT auth, tenant isolation. |
| **Studio** | A built-in chat and trace UI at `/studio`, served by the same process. No build step. |
| **Structured output** | Ask for a schema and get validated objects back, not prose to parse. |

---

## Packages

Six distributions, released together, one version. Install only what you need.

| Package | What it is |
|---|---|
| [`agentship-sdk`](https://pypi.org/project/agentship-sdk/) | The batteries-included meta-package |
| [`agentship-core`](https://pypi.org/project/agentship-core/) | The vendor-free kernel: spec, runtime, registry, middleware, `echo` engine |
| [`agentship-langgraph`](https://pypi.org/project/agentship-langgraph/) | The reference engine — compiles a spec into a LangGraph `StateGraph` |
| [`agentship-service`](https://pypi.org/project/agentship-service/) | The FastAPI app behind `/v1`, plus Studio |
| [`agentship-observability`](https://pypi.org/project/agentship-observability/) | The OpenTelemetry pipeline and its exporters |
| [`agentship-cli`](https://pypi.org/project/agentship-cli/) | `agentship run`, `serve`, `verify`, `doctor`, `init` |

`agentship-core` imports no vendor library. That is what keeps the seams honest: an engine
can be replaced without touching the kernel.

```bash
pip install "agentship-sdk[starter]"                 # kernel + LangGraph + CLI
pip install "agentship-sdk[starter,observability]"   # + the OTel pipeline
pip install "agentship-sdk[all]"                     # everything
pip install "agentship-langgraph[mcp]"               # MCP tools
pip install "agentship-core[postgres]"               # durable checkpoints
```

---

## Commands

```bash
agentship init my-project      # scaffold a project
agentship new-agent support    # scaffold one agent from a template
agentship run agent.yaml       # one turn, printed
agentship serve --agents-dir agents   # the /v1 API + Studio
agentship doctor               # validate every spec against its engine
agentship verify               # prove installed engines honour what they declare
agentship db upgrade           # apply checkpoint migrations (gated)
```

`doctor` and `verify` exist because a capability an engine *declares* and one it actually
*has* are different things. `verify` fails the build when they diverge.

---

## Reference

| | |
|---|---|
| Capability guides | [`docs/capabilities/`](docs/capabilities/) — one per pillar: [multi-agent](docs/capabilities/multi-agent.md), [tools & MCP](docs/capabilities/tools-and-mcp.md), [observability](docs/capabilities/observability.md), [service & security](docs/capabilities/service-and-security.md), [checkpointing & HITL](docs/capabilities/checkpointing-and-hitl.md) |
| Architecture decisions | [`docs/decisions/`](docs/decisions/) — why we integrate rather than reimplement |
| Runnable examples | [`examples/`](examples/) |
| Releasing and versioning | [`docs/RELEASING.md`](docs/RELEASING.md) |
| Deploying | [`deploy/RAILWAY.md`](deploy/RAILWAY.md) |
| API collection | [`postman/`](postman/) |
| Contributing | [`CONTRIBUTING.md`](CONTRIBUTING.md) |
| Changelog | [`docs/CHANGELOG.md`](docs/CHANGELOG.md) |

---

## Status

**Early — `0.x`, and the API can change between minor versions.** Pin exactly if that
matters to you (`agentship-sdk==0.0.1`).

> **Known issue in 0.0.1.** A `[starter]`-only install fails with
> `observability provider 'otel' is not installed`, because tracing is on by default and
> the adapter is not in that extra. Until 0.0.2, install
> `pip install "agentship-sdk[starter,observability]"`. Fixed on `main`.

Rebuilt foundation-first: one thin working slice per phase, with tests and a runnable demo
before anything is called done. Test first, one task per commit, CI green — see
[CONTRIBUTING](CONTRIBUTING.md).

## Licence

[Apache 2.0](LICENSE).
