<p align="center">
  <img src="branding/banners/github-banner@3x.png" alt="AgentShip" width="100%">
</p>

<h3 align="center">The production layer for AI agents.</h3>

<p align="center">
  Write <code>agent.py</code> + <code>agent.yaml</code>.<br>
  Get REST API, sessions, MCP tools, memory, and observability for free.
</p>

<p align="center">
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/Python-3.13+-3776AB?style=flat&logo=python&logoColor=white" alt="Python"></a>
  <a href="https://fastapi.tiangolo.com/"><img src="https://img.shields.io/badge/FastAPI-0.119-009688?style=flat&logo=fastapi&logoColor=white" alt="FastAPI"></a>
  <a href="https://github.com/google/generative-ai-python"><img src="https://img.shields.io/badge/Google_ADK-1.15-4285F4?style=flat&logo=google&logoColor=white" alt="Google ADK"></a>
  <a href="https://www.langchain.com/"><img src="https://img.shields.io/badge/LangGraph-Latest-121212?style=flat&logo=langchain&logoColor=white" alt="LangGraph"></a>
  <a href="https://modelcontextprotocol.io/"><img src="https://img.shields.io/badge/MCP-Integrated-FF6B35?style=flat" alt="MCP"></a>
  <a href="https://www.postgresql.org/"><img src="https://img.shields.io/badge/PostgreSQL-16-336791?style=flat&logo=postgresql&logoColor=white" alt="PostgreSQL"></a>
  <a href="https://opensource.org/licenses/MIT"><img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License: MIT"></a>
</p>

---

## Quick start

```bash
pip install agentship

agentship init my-project
cd my-project
agentship new-agent triage
agentship serve
```

That's a running production service. `POST /agents/triage/chat` is live.

```bash
curl -X POST http://localhost:7001/agents/triage/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Hello", "user_id": "u1"}'
# {"agent": "triage", "response": "Hi! How can I help you today?"}
```

Set `OPENAI_API_KEY` (or `ANTHROPIC_API_KEY` / `GOOGLE_API_KEY`) in `.env` and you're done.

---

## The problem

Every team building AI agents hits the same wall.

**Production plumbing.** Your agent works in the notebook. Shipping it means building a REST API, wiring PostgreSQL session storage, setting up observability, writing Docker configs, handling streaming. That's ~2,000 lines of infrastructure code and two weeks of work that has nothing to do with what your agent actually does. You rebuild it from scratch for every agent.

AgentShip is that plumbing, built once.

| | Without AgentShip | With AgentShip |
|---|---|---|
| Time to production (per agent) | 2 weeks | **~1 hour** |
| Infrastructure code | ~2,000 lines | **~10 lines** (agent logic only) |
| Observability setup | 2–3 days | **0** (built-in) |
| Session management | 2–3 days | **0** (built-in) |
| MCP tool integration | Manual per-framework | **Config declaration, auto-discovered** |

---

## Build an agent

Two files. Zero boilerplate.

**`agents/triage/agent.py`**
```python
from agentship import Agent

class TriageAgent(Agent):
    pass
```

**`agents/triage/agent.yaml`**
```yaml
agent_name: triage
engine: langgraph
model: openai/gpt-4o-mini
temperature: 0.4

system_prompt: |
  You are a helpful triage assistant.

memory:
  backend: memory          # or postgres

observability:
  backend: none            # or langfuse

mcp:
  servers: []
```

Run `agentship serve` — the agent is auto-discovered. No registration step.

### Domain knowledge with `__skills__/`

Drop `.md` files into `agents/<name>/__skills__/` and they're automatically injected into the system prompt — no code change needed.

```
agents/triage/__skills__/
├── intake-protocol.md     # loaded automatically
└── escalation-rules.md    # loaded automatically
```

### Session persistence

Two-line change to use Postgres:

```yaml
memory:
  backend: postgres
  url: "${AGENTSHIP_DATABASE_URL}"
```

### MCP tools

Declare the server, AgentShip connects at startup and injects tool schemas into the prompt:

```yaml
mcp:
  servers:
    - name: filesystem
      transport: stdio
      command: ["npx", "-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
```

### Observability (Langfuse)

```yaml
observability:
  backend: langfuse
```

Set `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` in `.env`. Every chat turn emits an OTEL span; every tool call emits a child span.

---

## Multiple agents

```bash
agentship new-agent billing
agentship new-agent escalation
agentship serve
```

Each agent gets its own route. No collision.

```
POST /agents/triage/chat
POST /agents/billing/chat
POST /agents/escalation/chat
```

---

## What's built in

| Capability | How it works |
|---|---|
| **REST API** | FastAPI, auto-registered per agent |
| **Session memory** | LangGraph `AsyncPostgresSaver` or `InMemorySaver` |
| **MCP tools** | STDIO transport, schema auto-discovery, prompt injection |
| **Domain knowledge** | `__skills__/*.md` loaded into system prompt |
| **Observability** | OTEL spans → Langfuse (or no-op) |
| **LLM routing** | LiteLLM — any provider via `model: provider/model` |
| **Error messages** | Readable, not raw tracebacks |

---

## Docker development (existing framework)

The full framework with ADK + LangGraph dual-engine support, AgentShip Studio, and streaming runs via Docker:

```bash
git clone https://github.com/Agent-Ship/agent-ship.git
cd agent-ship
make docker-setup   # creates .env, builds, starts everything
```

| Service | URL |
|---|---|
| API + Swagger | http://localhost:7001/swagger |
| AgentShip Studio | http://localhost:7001/studio |
| Docs | http://localhost:7001/docs |

```bash
make docker-up      # start (subsequent runs)
make docker-logs    # tail logs
make docker-down    # stop
```

The Docker setup uses the `src/` directory structure with the existing `BaseAgent` pattern, dual ADK/LangGraph engines, and full MCP HTTP/OAuth support. The new `agentship` CLI is the simpler distribution path for new projects.

---

## Example agents (Docker framework)

| Agent | Demonstrates |
|---|---|
| [`single_agent_pattern/`](src/all_agents/single_agent_pattern/) | Minimal two-file agent |
| [`orchestrator_pattern/`](src/all_agents/orchestrator_pattern/) | Sub-agents as tools |
| [`tool_pattern/`](src/all_agents/tool_pattern/) | Custom function tools |
| [`file_analysis_agent/`](src/all_agents/file_analysis_agent/) | PDF and document parsing |
| [`postgres_adk_mcp_agent/`](src/all_agents/postgres_adk_mcp_agent/) | STDIO MCP · ADK engine |
| [`postgres_langgraph_mcp_agent/`](src/all_agents/postgres_langgraph_mcp_agent/) | STDIO MCP · LangGraph engine |
| [`github_adk_mcp_agent/`](src/all_agents/github_adk_mcp_agent/) | HTTP/OAuth MCP · ADK engine |
| [`github_langgraph_mcp_agent/`](src/all_agents/github_langgraph_mcp_agent/) | HTTP/OAuth MCP · LangGraph engine |

---

## Reference

<details>
<summary><strong>CLI commands</strong></summary>

```bash
agentship init <project>       # scaffold agentship.toml, agents/, .env
agentship new-agent <name>     # create agents/<name>/agent.py + agent.yaml + __skills__/
agentship serve                # start FastAPI, auto-register all agents
agentship serve --port 8080    # custom port (default: 7001)
agentship serve --reload       # hot-reload on file changes
```

</details>

<details>
<summary><strong>agent.yaml reference</strong></summary>

```yaml
agent_name: my_agent
engine: langgraph                       # only supported engine in CLI
model: openai/gpt-4o-mini               # LiteLLM format: provider/model
temperature: 0.4
max_tool_rounds: 10

system_prompt: |
  You are a helpful assistant.

memory:
  backend: memory                       # memory | postgres
  url: "${AGENTSHIP_DATABASE_URL}"      # required for postgres

observability:
  backend: none                         # none | langfuse
  # env vars: LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY

mcp:
  servers:
    - name: myserver
      transport: stdio
      command: ["npx", "-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
      env:
        MY_VAR: "${MY_ENV_VAR}"
```

</details>

<details>
<summary><strong>Environment variables</strong></summary>

```bash
# LLM provider (at least one required)
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
GOOGLE_API_KEY=...

# Session persistence (optional — defaults to in-memory)
AGENTSHIP_DATABASE_URL=postgresql://user:pass@localhost:5432/agentship

# Observability (optional)
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_HOST=https://cloud.langfuse.com
```

</details>

<details>
<summary><strong>Docker make commands</strong></summary>

```bash
make docker-setup    # first-time setup
make docker-up       # start
make docker-down     # stop
make docker-restart  # restart
make docker-reload   # hard rebuild + restart
make docker-logs     # tail logs
make dev             # local (no Docker), localhost:7001
make test            # run all tests
make lint            # flake8
make format          # black
make heroku-deploy   # one-command Heroku deploy
```

</details>

---

## Roadmap

**Now:** `agentship` CLI · LangGraph engine · Postgres sessions · MCP STDIO · Langfuse observability · `__skills__` domain knowledge · ADK engine (Docker) · MCP HTTP/OAuth (Docker) · AgentShip Studio

**Next:** Architecture eval matrix (`agentship eval`) — compare ADK vs LangGraph on latency, cost, and reliability for your specific workload · Long-term memory (pgvector, mem0) · Presidio PII redaction · A2A protocol

**Later:** PyPI publish · Docker image · AgentShip Hub (community agent registry) · OpenAI Agents SDK engine · CrewAI engine

---

## Contributing

Check [`good first issue`](https://github.com/Agent-Ship/agent-ship/labels/good%20first%20issue), read [`CLAUDE.md`](CLAUDE.md) for the developer guide, and [`CONTRIBUTING-AGENTS.md`](CONTRIBUTING-AGENTS.md) if you're using a coding agent to scaffold changes.

```bash
make test && make lint
```

---

<p align="center">MIT License · Built by <a href="https://github.com/Agent-Ship/agent-ship">AgentShip</a> · Open source, no vendor allegiance</p>
