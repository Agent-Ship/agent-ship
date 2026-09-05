# Service & security

Serve any AgentShip agent over a secure `/v1` REST/SSE/WebSocket surface, with
pluggable authentication and the one guarantee no gateway can give you:
per-tenant isolation on every stored row.

## What it is

- **Serve any agent over REST / SSE / WS.** `create_app()` mounts a `/v1`
  surface: `POST /v1/agents/{name}:invoke` (structured JSON), `POST
  /v1/agents/{name}:stream` (SSE), `WS /v1/agents/{name}/live`, `GET /v1/agents`
  discovery, and a `/v1/tasks` skeleton. Streaming is `sse-starlette`'s
  `EventSourceResponse` — we do **not** hand-roll the SSE wire, keepalive, or
  disconnect handling.
- **Pluggable auth behind one seam.** An `AuthProvider` ABC turns a request into
  a `Caller`. Three thin adapters implement it: `ForwardedHeaderAuthProvider`
  (trust the identity agentgateway already verified — the production path),
  `ApiKeyAuthProvider` (secure gateway-free dev/CI), and an optional
  `JwtAuthProvider` (OIDC without a gateway, backed by PyJWT's `PyJWKClient`).
  `CompositeAuthProvider` just dispatches by credential scheme.
- **Per-tenant isolation.** `authorize(caller, agent=…, verb=…)` enforces
  per-agent scopes; `TenantScope` binds `caller.tenant_id`/`user_id` into a
  contextvar every store reads, so a session or task from another tenant is a
  miss or a `TenantViolation`. Identity comes only from the `Caller`, never the
  request body.
- **HTTP security posture.** CORS allow-list (never `*` with credentials),
  always-on security headers (`SecurityHeadersMiddleware`: nosniff / no-referrer
  / frame-deny, HSTS when TLS-forwarded), and an optional off-by-default
  in-process limiter (`RateLimitMiddleware`) — real rate-limit/mTLS/RBAC is
  agentgateway's job (P05), not rebuilt here.

## How to use it

`agentship serve` is the supported launch path. It is doctor-gated — every
`agents/*.yaml` and the auth provider are validated *before* the socket binds,
so a bad spec or misconfigured provider fails fast (exit `1`). It defaults to
loopback (`127.0.0.1`).

```bash
# One dev key: a JSON list of {key, user, tenant, scopes} entries, SHA-256'd by EnvApiKeyStore.
export AGENTSHIP_API_KEYS='[{"key":"dev-secret","user":"me","tenant":"acme","scopes":["agent:*:invoke","agents:list"]}]'
agentship serve --auth api_key --agents-dir agents   # /v1 at http://127.0.0.1:8000
```

```bash
# Hit the real route (note the AIP-style :invoke verb and the Bearer key):
curl -X POST http://127.0.0.1:8000/v1/agents/assistant:invoke \
  -H "Authorization: Bearer dev-secret" \
  -H "Content-Type: application/json" \
  -d '{"input": "hello"}'
```

The auth provider is chosen with `--auth` (`api_key | forwarded | jwt |
composite`). In production you run behind agentgateway with `--auth forwarded`,
which reads the tenant/user/scopes the gateway verified and forwarded.

## One runnable example

`examples/frontend/backend_caller.py` is a server-to-server caller: it holds the
API key and calls `:invoke` and `:stream`. `examples/frontend/bff/app.py` is the
recommended browser pattern — a BFF that keeps the key server-side and proxies
SSE back to a same-origin page (the browser never holds a credential).

```python
from agentship.auth import ApiKeyAuthProvider, EnvApiKeyStore
from agentship_service.app import create_app

app = create_app(auth=ApiKeyAuthProvider(EnvApiKeyStore()))  # ASGI app; run under uvicorn
```

## Status & limits

🟨 in-flight, 40/44. Open: `agentship deploy` CLI (Docker/Procfile exist, the
command is missing), Postman collection verify against the current `/v1`, and
the `dev_ingress_locked_down` lockdown cell (blocked — the legacy `/api/*`
router is intentionally not built). Authoritative status: `.spec-dev/STATUS.md`.
