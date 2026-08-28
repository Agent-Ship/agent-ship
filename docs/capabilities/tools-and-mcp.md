# Tools & MCP

Any AgentShip agent can call real tools — first-party ones you register and the
entire MCP ecosystem — declared in YAML, executed through the engine, with no
vendor lock-in.

## What you get

- **Native tools.** A vendor-neutral `Tool` (name + description + Pydantic
  `args_schema` + callable) that the model calls. Four built-ins ship in
  `agentship.tools.builtins` — `calculator`, `http_request`, `web_search`,
  `scrape_url` — and a `tools:` entry is either a registered name or a
  `module:function` pointing at your own.
- **MCP servers.** An `mcp:` block declares local (`stdio`) *and* remote
  (`streamable_http`) servers in one place. Their tools are discovered and bound
  alongside native ones — **an MCP tool and a native tool look identical to the
  agent**, which is the whole point.
- **Skills.** A `skills:` reference points at a `SKILL.md` folder that teaches the
  model *how* to use a tool. Distinct from `tools:`, which is the executable thing.
- **A flooding guard.** `allowed_tools:` is an optional allow-list applied to
  native and MCP tools alike — the answer to "I connected three MCP servers and now
  the model sees 200 tools".
- **Autonomous tool use.** `template: autonomous` wraps the `deepagents` library, so
  one agent plans and calls its own tools in a loop with zero author code.

## What we consume vs. what we build

| Concern | Who does it | Why |
|---|---|---|
| MCP protocol, transports, OAuth | **`langchain-mcp-adapters` → official `mcp` SDK** (consumed) | We do not hand-roll an MCP client. See [ADR 0004](../decisions/0004-consume-langchain-mcp-adapters.md). |
| Autonomous tool loop | **`deepagents`** (consumed) | A planner-executor loop is a solved problem. |
| The `Tool` type | **We build it** — vendor-free | Core cannot import LangChain, and the same `Tool` must satisfy every engine. |
| Config surface + lifecycle | **We build it** — the `mcp:` block, the client's lifetime | Declaring servers in YAML and closing them cleanly is ours. |
| Presenting both as one list | **We build it** — the bind step | This is the capability: one uniform surface over two very different sources. |

The thin part is deliberate: we own the *contract* (`Tool`, the `mcp:` block) and let
the ecosystem own the *protocol*.

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

## Swapping the vendor

`Tool` lives in the vendor-free kernel, so it is what a second engine adapter binds too —
nothing about a tool definition is LangChain-shaped. Swapping an MCP server from local to
remote is a transport change in YAML (`stdio` → `streamable_http`) with no code change, and
swapping the MCP *client* would touch one adapter, because the `mcp:` block is our contract
rather than a passthrough of the library's config.

## Where the pieces live

- `agentship.tools.tool` — the vendor-free `Tool` type
- `agentship.tools.registry` — name → `Tool` resolution
- `agentship.tools.builtins` — `calculator`, `http_request`, `web_search`, `scrape_url`
- `agentship.primitives.idempotency` — `call_once` / `idem_key` for side-effecting tools

## Status & limits

🟨 in-flight, 26/28. Two open items, both proof gaps rather than missing features:

- **No graceful tool-error test.** A tool that raises should degrade into an error the model
  can see and recover from, not a crashed run. The behaviour is not yet pinned by a test.
- **No combined demo.** Native tools and MCP tools are each demonstrated separately; nothing
  yet shows one agent using both *in the same turn*, which is the claim this page makes in
  its first paragraph.

Authoritative status: `.spec-dev/STATUS.md`.
