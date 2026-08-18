# Testing AgentShip with Postman

This folder carries forward the old repo's Postman parity: an importable collection and
environment that exercise the `/v1` runtime-service surface end to end.

## Files

- `AgentShip.postman_collection.json` — the requests (discovery, invoke, stream, tasks).
- `AgentShip.postman_environment.json` — `baseUrl`, `agent`, `apiKey`, `taskId` variables.

No secrets are hardcoded: the API key lives in the environment (defaulting to the dev key
`dev` that `docker-compose.yml` seeds), and `taskId` is captured automatically from
**Create task** into the environment for the follow-up task requests.

## First request

1. Boot the service: `make docker-up` (API on `http://localhost:8000`, seeded with the dev
   key `dev`). Or run locally: `agentship serve --agents-dir examples`.
2. In Postman, **Import** both files and select the **AgentShip (local)** environment.
3. Send **Health (public)** — expect `200 {"status":"ok"}` (this request sends no auth).
4. Send **List agents** — the `X-API-Key: dev` header is applied from the environment;
   expect the `hello` agent card.
5. Send **Invoke agent** — `POST /v1/agents/hello:invoke` with `{"input":"hello"}` returns
   the structured `InvokeResponse`.
6. Send **Stream agent (SSE)** to watch the `session → content → done` event frames.
7. Send **Create task** (captures `taskId`), then **Get task** / **List tasks** /
   **Cancel task**.

## Auth

Every request except **Health** authenticates with `X-API-Key: {{apiKey}}`. To test a
different tenant or scope, add another entry to `AGENTSHIP_API_KEYS` (JSON) and point
`apiKey` at it. A missing/unknown key returns `401`; a key without
`agent:{name}:invoke` scope returns `403`.
