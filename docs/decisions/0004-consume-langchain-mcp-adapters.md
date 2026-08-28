# 0004 — Consume `langchain-mcp-adapters` for MCP; never hand-roll a client

**Status:** accepted · **Scope:** P04 (tools & MCP), consumed by P18 (A2A gateway)

## Context

MCP is how an agent reaches the wider tool ecosystem, so AgentShip has to speak it. There are
three ways to do that:

1. Write our own MCP client on the official `mcp` SDK.
2. Consume `langchain-mcp-adapters`' `MultiServerMCPClient`, which already sits on that SDK.
3. Federate everything through a gateway and never speak MCP in-process at all.

Option 1 is the trap. An MCP client is not one thing — it is stdio and `streamable_http`
transports, session lifecycle, capability negotiation, tool discovery, notification handling,
and OAuth for remote servers. All of it moves with a spec that is still revising. Writing it
ourselves means owning a protocol implementation forever, in a project whose entire thesis is
that we should not be doing that.

## Decision

**Consume `langchain-mcp-adapters` (`MultiServerMCPClient`) for direct local and remote MCP.**
The protocol, both transports, and OAuth come from the library, which sits on the official `mcp`
SDK. We pin `mcp>=1.28,<2` targeting 2025-06-18 transport/auth semantics, and `agentship doctor`
flags an out-of-range install rather than letting it break subtly at runtime.

**We build exactly three thin things:**

1. **The `mcp:` config surface** — local and remote servers declared in one YAML block. This is
   our contract, not a passthrough of the library's config shape, which is what keeps the
   library swappable.
2. **`TokenStorage` persistence** — OAuth tokens have to outlive a process; the library does not
   decide where our secrets live.
3. **Lifecycle** — opening the client with the agent and closing it cleanly.

**And one presentation rule:** MCP tools are bound alongside native tools as the same `Tool`
type, so the model cannot tell them apart. That uniformity is the capability; the protocol is
plumbing.

The MCP extra is optional and imported lazily, so a bare install has no MCP dependency at all.

Federation (option 3) is not rejected, just scoped elsewhere: agentgateway's virtual-MCP handles
many-servers-behind-one-endpoint in P18. Direct MCP here, federation there.

## Consequence

**Bought:** transports, session handling, and OAuth arrive maintained, and MCP spec revisions are
someone else's release note rather than our sprint. What we own — a YAML block, token storage,
and a lifecycle — is small enough to read in one sitting.

**Cost:** a dependency on a LangChain-ecosystem package. Contained deliberately: it lives in the
`agentship-langgraph` adapter behind the `[mcp]` extra, and `agentship-core` never imports it, so
the kernel stays vendor-free and a future non-LangChain engine can bring its own MCP client
behind the same `mcp:` block.

**Do not break:** do not let the library's config shape become the YAML surface. The moment
`mcp:` is a passthrough, the library stops being swappable and this ADR becomes false. And do not
add MCP to the base install — the lazy import is what keeps a bare AgentShip free of it.
