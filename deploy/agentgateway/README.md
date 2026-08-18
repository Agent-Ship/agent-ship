# agentgateway front door (optional Layer 1)

AgentShip runs fine with **no gateway** — one service, many agents, direct per-agent MCP, and
in-process specialists (Layer 0, the default). When you need to front many agents and MCP servers at
scale — edge auth, mTLS, RBAC, rate-limit, routing, and one federated MCP endpoint — put
[agentgateway](https://agentgateway.dev) (Rust, Apache-2.0) in front. We **consume** it rather than
reinvent it (DESIGN §2.1 · G2): it provides exactly the security/routing work a bespoke gateway
would hand-roll, and it fronts **both** A2A agents and MCP servers.

This folder is a **recipe**, not a vendored binary:

| File | What it is |
|---|---|
| `config.yaml` | agentgateway config: A2A front door (port 8080) + virtual-MCP federation (port 8081) |
| `docker-compose.yaml` | an `interop` profile that runs the gateway in front of the app — **off by default** |

## Layer 0 → Layer 1 is a config change, never a code change

The phase invariant: **interop is configuration; the agent's `build()` never changes.** Switching an
agent onto the gateway edits only its YAML declarations.

Direct MCP (Layer 0, default):

```yaml
mcp:
  mode: direct          # per-agent MCP clients (P03)
  servers: [postgres, github]
```

Federated MCP through the gateway (Layer 1):

```yaml
mcp:
  mode: gateway         # tools come from the aggregator
  servers: [gateway]    # one entry → the gateway's /mcp endpoint
```

The agent now connects to a single Streamable-HTTP MCP server (the gateway) and calls namespaced
tools (`db_query`, `gh_search_issues`). Its graph/build code is byte-for-byte identical.

## Bring it up

```bash
# Layer 0 — the app alone (default):
docker compose up

# Layer 1 — app + gateway (explicit profile):
docker compose -f docker-compose.yml -f deploy/agentgateway/docker-compose.yaml \
    --profile interop up
```

Set the `${...}` values (OIDC issuer/JWKS for edge auth, upstream MCP connection details, the OTLP
collector) in your environment or `.env` first — see `docker-compose.yaml` for the full list.

## What the gateway owns vs what the app owns

- **Gateway (edge):** JWT/OIDC + mTLS, per-copy rate-limit, path/header routing, agent-card URL
  rewriting, tool-level RBAC (CEL), virtual-MCP federation, OpenTelemetry.
- **App (irreducible core, P04):** its own `AuthProvider` + **tenant isolation** (no gateway can
  give the per-tenant data guarantee), the SSE contract, the A2A method mapping. Defense in depth —
  the gateway authenticates the peer, the app authorizes the verb and scopes the tenant.

Rate-limit note (DESIGN §2.1): agentgateway's free build covers **per-copy** limits; **global**
(shared-across-copies) limits are a paid tier. Pair the edge limit with LiteLLM per-key budgets for
provider-side spend caps.

## Verifying the recipe (the `mcp_federation_virtual_mcp` conformance cell)

The parity cell — *an agent pointed at the gateway's single `/mcp` gets the same aggregated, prefixed
tools and results as direct P03 MCP* — is a **live-gateway** check: it needs a running agentgateway
process and real upstreams, so it lives in this integration profile, not the CI-gate tier. The
CI-gate P05 cells (`a2a_server_card`, `a2a_specialist_transparency`, `interop_optional`) run without
a gateway in `conformance/test_phase05_agent_gateway.py`.

## Fallback (only on a documented trigger)

Default is **consume** agentgateway's virtual-MCP. If it proves genuinely insufficient (A2A depth
gaps outside Kubernetes, or missing MCP push-notifications), evaluate
[IBM ContextForge](https://github.com/IBM/mcp-context-forge) as the backup federation peer **before**
hand-rolling anything. A thin in-tree FastAPI aggregator is a last resort, built only when both are
rejected and the gap is recorded (revises the phase's OQ3: consume, don't build).
