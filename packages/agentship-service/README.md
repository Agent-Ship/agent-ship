# agentship-service

The AgentShip **runtime service** — the REST / SSE / WebSocket surface that turns a built
agent into an HTTP API, with pluggable authentication adapters and, above all, **tenant
isolation**.

This is the part of the stack a gateway *cannot* replace. A gateway (agentgateway) routes
requests and can authenticate them at the edge; but only the app can guarantee that every
stored read and write is scoped to the authenticated tenant. That guarantee lives here.

```python
from agentship.auth import ApiKeyAuthProvider, EnvApiKeyStore
from agentship_service import create_app

app = create_app(auth=ApiKeyAuthProvider(EnvApiKeyStore()))
```

## Integrations (we wire, we don't reinvent)

- **Streaming** — `/v1 …:stream` and A2A `message/stream` frame Server-Sent Events with
  [`sse-starlette`](https://github.com/sysid/sse-starlette)'s `EventSourceResponse`, which owns
  the wire encoding, keepalive comments, and client-disconnect cancellation. We only shape each
  `StreamEvent` into its event/data fields.
- **Auth** — `agentship.auth` ships pluggable providers; the optional OIDC path
  (`JwtAuthProvider`) delegates JWKS fetching, key-id resolution, and rotation to PyJWT's
  `PyJWKClient`. Tenant isolation on every read/write is the part that stays here.
- **A2A** — we speak the protocol with our own thin Pydantic wire models rather than pull in the
  protobuf-first `a2a-sdk`. A drift guard under the `agentship-service[a2a]` extra
  (`tests/test_a2a_conformance.py`) validates every AgentCard / Message / status frame against
  `a2a-sdk`'s own schema, so we cannot drift from the spec. See
  [`docs/decisions/0001-integrate-not-invent.md`](../../docs/decisions/0001-integrate-not-invent.md).

See `agentship serve` for the one-command server, and the phase-04 spec for the full
endpoint contract.
