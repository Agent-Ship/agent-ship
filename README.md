# AgentShip

Author an agent — or a team of agents — in YAML (or Python), and get the production
plumbing wired for you: models, tools, structured handoffs between agents, memory,
observability, structured output, streaming, and serving. AgentShip **integrates
best-of-breed libraries** (LangGraph, LiteLLM, MCP, mem0, OpenTelemetry/Langfuse)
behind small, stable seams. It never reinvents them.

```yaml
# team.yaml
name: research-team
engine: langgraph
template: supervisor
members:
  - name: researcher
    model: openai/gpt-4o
    prompt: Find and summarize the facts.
  - name: writer
    model: anthropic/claude-sonnet-4-6
    prompt: Write the final answer from the research.
```

```bash
agentship run team.yaml --input "What changed in EU AI regulation this year?"
agentship serve team.yaml      # REST + SSE on :7001
```

## Status
Rebuilt from the ground up. Foundation-first, one thin working slice per phase.
See [`.spec-dev/DESIGN.md`](.spec-dev/DESIGN.md) for the design, [`.spec-dev/PHASES.md`](.spec-dev/PHASES.md)
for the phase ladder and live progress, and [`docs/decisions/`](docs/decisions/) for the
architecture decision records (e.g. how we integrate best-of-breed libraries and guard against
drifting from their specs).

## Develop
Every change follows [`.spec-dev/operating-model.md`](.spec-dev/operating-model.md):
test-first, one task per commit, CI green, demo updated. Nothing is marked done until
it works end-to-end and a test proves it.
