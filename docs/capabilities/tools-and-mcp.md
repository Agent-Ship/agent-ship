# Tools & MCP

Any AgentShip agent can call real tools — first-party ones you register and the
entire MCP ecosystem — declared in YAML, executed through the engine, with no
vendor lock-in.

## What it is

- **Native tools.** A vendor-neutral `Tool` (name + description + Pydantic
  `args_schema` + callable) that the model calls. Built-ins ship in
  `agentship.tools.builtins` (`calculator`, `http_request`, `web_search`); a
  `tools:` reference is either a registered name or a `module:function`.
- **MCP servers via `MultiServerMCPClient`.** An `mcp:` block declares local
  (`stdio`) *and* remote (`streamable_http`) servers in one place. Their tools
  are discovered by `langchain-mcp-adapters`' `MultiServerMCPClient` and bound
  alongside native tools — an MCP tool and a native one look identical to the
  agent. We do **not** hand-roll an MCP client; the protocol, transports, and
  OAuth come from the library on the official `mcp` SDK.
- **Autonomous tool use.** The `template: autonomous` archetype wraps the
  `deepagents` library's `create_deep_agent`, so one agent plans and calls its
  own tools in a loop — zero author code.

## How to use it

Declare native tools with `tools:` and MCP servers with `mcp:` on the same
`AgentSpec`, then build and run. Nothing else changes — the engine resolves and
binds both sets.

```yaml
# assistant.yaml
name: assistant
engine: langgraph
template: single
model: openai/gpt-4o-mini
tools:
  - calculator                       # a registered built-in Tool
mcp:
  time:                              # a LOCAL stdio MCP server
    transport: stdio
    command: python3
    args: [time_server.py]
  # remote would be: {transport: streamable_http, url: https://…, headers: {...}}
prompt: Prefer the available tools over doing the work yourself.
```

```python
from agentship import build_agent

agent = build_agent("assistant.yaml")           # resolves native tools + MCP
result = await agent.run("How many days from 2026-01-01 to 2026-08-14?")
print(result.output)
```

The MCP extra is optional and imported lazily — install it with
`pip install "agentship-langgraph[mcp]"`. `agentship doctor` guards the `mcp`
pin (`mcp>=1.28,<2`, targeting 2025-06-18 transport/auth semantics); an
out-of-range install is flagged, not silently broken.

## One runnable example

`agentship-demo/agents/mcp/agent.yaml` — a `single`-template agent that connects
to a local stdio MCP server (`agents/mcp/time_server.py`), discovers its
`days_between` tool, and calls it. A SKILL.md folder (`skills/date-math`) teaches
the model *how* to use that tool. Run it:

```bash
pip install "agentship-langgraph[mcp]"
agentship run agents/mcp/agent.yaml --input "How many days from 2026-01-01 to 2026-08-14?"
```

A remote server would look identical, with `transport: streamable_http` and a
`url:` instead of `command`/`args`. Offline tests prove the guidance reaches the
prompt; the live path proves the real MCP call.

## Status & limits

🟨 in-flight, 26/28. Open: graceful tool-error handling test; combined
native-skill + MCP-tool demo. Authoritative status: `.spec-dev/STATUS.md`.
