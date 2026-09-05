# Agent gateway / A2A

Expose any AgentShip agent over the A2A (agent-to-agent) protocol, and consult
remote agents as if they were in-process — one call site, either side of the wire.

## What it is

- **Expose an agent as an A2A endpoint.** Set `a2a.expose: true` on an
  `AgentSpec` and the service publishes a capability-honest Agent Card at
  `/.well-known/agent-card.json` plus a JSON-RPC endpoint that speaks
  `message/send` and `message/stream`. Non-exposed (Layer 0) agents are 404 on
  the wire — they never leak onto the network.
- **Call remote agents transparently.** A member declared with an `a2a:` block
  resolves to a `RemoteA2aAgent` that satisfies the same `Specialist.send` seam
  as an in-process `LocalSpecialist`, so a supervisor's `specialist(name)`
  call reads identically whichever side of the wire the specialist lives on.
- **Conformance-guarded wire models.** We keep thin, vendor-free Pydantic wire
  models (`agentship.a2a.models` — `AgentCard`, `Message`, `JsonRpcRequest`)
  instead of adopting `a2a-sdk`'s protobuf-first types. A drift guard
  (`test_a2a_conformance.py`, under the `agentship-service[a2a]` extra) validates
  every shape we emit against `a2a-sdk`'s own schema, so we conform to the spec
  without taking a dependency that fits this JSON service poorly.

## How to use it

Opt an agent into A2A with an `a2a:` block (default-deny — an exposed agent must
name at least one security scheme):

```yaml
# triage.yaml
name: triage
engine: langgraph
template: single
model: openai/gpt-4o-mini
a2a:
  expose: true
  security: [oauth2]   # enforced by the router AND advertised on the card (one set, no drift)
prompt: Route the message to the right specialist.
```

Fetch its Agent Card, then send it an A2A message:

```bash
# Public discovery (RFC 8615 .well-known) — no auth
curl http://localhost:8000/a2a/triage/.well-known/agent-card.json

# Authenticated JSON-RPC call (needs the a2a:invoke scope)
curl -X POST http://localhost:8000/a2a/triage \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"jsonrpc":"2.0","id":"1","method":"message/send",
       "params":{"message":{"role":"user","parts":[{"kind":"text","text":"hello"}]}}}'
```

Card and index live at `GET /a2a/{name}/.well-known/agent-card.json` and
`GET /.well-known/agents.json`; the JSON-RPC endpoint is `POST /a2a/{name}`. The
POST route runs under the same P04 auth middleware as `/v1` and additionally
checks the distinct `a2a:invoke` verb, so it inherits auth, TLS, and rate-limit.

## One runnable example

Consult a remote agent from a supervisor — the call site never branches on
transport. A member with an `a2a:` block becomes a `RemoteSpecialist`; a member
with a local `ref:` becomes a `LocalSpecialist`; both expose `send`:

```python
from agentship.a2a import (
    AgentRef,
    RemoteSpec,
    HttpxTransport,
    RemoteA2aAgent,
    RemoteSpecialist,
)

remote = RemoteSpec(url="https://rad.internal/a2a/radiology")
agent = RemoteA2aAgent(remote, transport=HttpxTransport(timeout_seconds=60))
radiology = RemoteSpecialist("radiology", agent, ctx)  # ctx = the current RunContext

finding = await radiology.send("read this chest x-ray")  # → message/send over the wire
```

`RemoteA2aAgent.send` builds the `message/send` JSON-RPC envelope, layers the
outbound credential over the tenant/trace propagation headers, and unwraps the
reply text; any transport failure becomes a retryable `A2AUnavailable`. The
Agent Card is generated from the spec plus the engine's real
`EngineCapabilities` via `build_agent_card` — `streaming` is true only when the
engine actually streams.

## Status & limits

🟨 in-flight, 15/24. Core A2A done (39 tests). Open: gateway fallback decision
doc; task-bridge + push BLOCKED on P11's `on_state_change` hook;
`specialist()` helper; ADK `to_a2a()` mount; virtual-MCP parity.
Authoritative status: `.spec-dev/STATUS.md`.
