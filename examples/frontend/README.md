# Frontend integration patterns

Two ways to call the AgentShip `/v1` surface from an application, plus a server-to-server
sample. All run against a service started with `agentship serve --agents-dir examples` (or
`make docker-up`) and an API key in `AGENTSHIP_API_KEYS`.

## 1. BFF (backend-for-frontend) — recommended for browsers

`bff/app.py` is a small FastAPI server that holds the service API key server-side,
authenticates the browser with its own session, and relays AgentShip's SSE stream to a
same-origin `/chat` route. **The browser never holds an AgentShip credential.**

```bash
AGENTSHIP_BASE_URL=http://localhost:8000 AGENTSHIP_API_KEY=dev \
    uvicorn examples.frontend.bff.app:app --port 3000
# open http://localhost:3000/
```

This is the default recommendation: a leaked browser bundle never exposes a long-lived key,
and the BFF can enforce its own per-user policy before proxying.

## 2. Short-lived scoped JWT — for SPAs/mobile calling `/v1` directly

When a client must call `/v1` directly, a trusted backend mints a **short-lived** (≈5 min)
JWT scoped to a single `agent:{name}:invoke`, and the service runs the `jwt` auth provider
to validate it (`agentship serve --auth jwt`). Because the token is short-lived and
narrowly scoped, a leak is low-blast-radius. For SSE the token rides as `?access_token=`
(headers are unavailable to `EventSource`); for WebSocket use the `bearer,<token>`
subprotocol, never a query param (tokens in URLs leak to logs). Minting is a few lines with
any JWT library against the same signing key/JWKS the provider validates — see the phase
design (§C6) for the claim set.

## 3. Server-to-server — trusted backends

`backend_caller.py` is the simplest case: a trusted backend calls `/v1` directly with a
service API key.

```bash
AGENTSHIP_BASE_URL=http://localhost:8000 AGENTSHIP_API_KEY=dev \
    python examples/frontend/backend_caller.py hello "hello there"
```

It prints the structured invoke response, then the streamed event frames.
