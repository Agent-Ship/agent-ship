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

See `agentship serve` for the one-command server, and the phase-04 spec for the full
endpoint contract.
