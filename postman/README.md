# Testing AgentShip with Postman

An importable, executable collection for the AgentShip runtime service's `/v1` surface:
health, agent discovery, invoke, SSE streaming, durable tasks, and the auth + RFC-9457
error contract. Every request carries test scripts, so this runs under `newman` in CI as
well as by hand in Postman.

## Files

| File | What it is |
|---|---|
| `AgentShip.postman_collection.json` | The requests, in four folders: Health, Agents, Tasks, Auth and errors. |
| `AgentShip.postman_environment.json` | The variables: `base_url`, `api_key`, `api_key_without_scope`, `agent_name`, plus `session_id` / `task_id` which the run fills in. |

No real secret is in either file. `api_key` defaults to the throwaway dev key that
`docker-compose.yml` seeds; anything else must be supplied at run time.

## Start the server

The collection needs **two** API keys: one with full scope (200s) and one deliberately
scoped to a *different* agent so the 403 request has something to prove. `docker-compose`
seeds only the first, so for a full run start the server yourself:

```bash
cd agentship
AGENTSHIP_API_KEYS='[
  {"key":"dev-key","user":"dev","tenant":"dev","scopes":["*"]},
  {"key":"limited-key","user":"limited","tenant":"dev","scopes":["agent:other:invoke"]}
]' .venv/bin/agentship serve --agents-dir examples --host 127.0.0.1 --port 8000
```

`--agents-dir examples` serves every `*.yaml` directly in `examples/` (8 agents at the time
of writing). `serve` is doctor-gated: an invalid spec or an uninstalled engine fails before
the socket binds.

The collection targets `hello` by default, which runs on the `echo` engine and needs no
provider key — that is what makes the whole run work offline. Point `agent_name` at
`assistant` or another example to drive a real model, and export the matching provider key
(`OPENAI_API_KEY`, …) before starting the server.

`make docker-up` also works (API on `:8000`, key `dev`), but its single seeded key has
wildcard scopes, so the 403 request will fail against it.

## Run it in Postman

1. **Import** both files.
2. Select the **AgentShip (local)** environment; set `api_key` (and
   `api_key_without_scope`) to the keys you started the server with.
3. Send **Health check** first — it sends no credential and should return
   `200 {"status":"ok"}`.
4. Run the folders in order. **Invoke agent** captures `session_id`; **Create task**
   captures `task_id`; the requests after them depend on those captures.

## Run it with newman

```bash
npx newman run postman/AgentShip.postman_collection.json \
  -e postman/AgentShip.postman_environment.json \
  --env-var base_url=http://127.0.0.1:8000 \
  --env-var api_key=dev-key \
  --env-var api_key_without_scope=limited-key
```

Passing the keys with `--env-var` keeps them out of the committed environment file. Order
matters — the collection is written to run top to bottom in one iteration.

## What each folder checks

**Health** — `GET /healthz`, the one public path (along with `/docs`, `/redoc`,
`/openapi.json`, and `/.well-known/*`). Also asserts the `x-trace-id` response header.

**Agents** — `GET /v1/agents` and `/v1/agents/{name}` for discovery;
`POST /v1/agents/{name}:invoke` for one turn, run twice to show a `session_id` threading a
conversation; `POST /v1/agents/{name}:stream` for SSE. The stream test parses the frames
and asserts the contract directly: the opening frame is `session` at `seq` 0, every `type`
is in the allowed union, and `seq` is gap-free and monotonic.

**Tasks** — create (202, `pending`), read, list, cancel. The task executor is not built
yet, so a task never leaves `pending` on its own; what this surface proves today is the
tenant-ownership contract, not execution.

**Auth and errors** — the four failure modes, each asserting `application/problem+json`
and the RFC-9457 body (`type`, `title`, `status`, `detail`, plus our `code` and
`trace_id`):

| Request | Status | `code` |
|---|---|---|
| No credential | 401 | `no_credentials` |
| Unknown key | 401 | `invalid_api_key` |
| Key without the agent's scope | 403 | `forbidden` |
| Unknown agent | 404 | `not_found` |
| Invalid body | 422 | `invalid_request` |

## Notes on the API this collection exercises

- **Invoke and stream are both `POST`**, and the verb is part of the path
  (`{name}:invoke`, `{name}:stream`) — there is no `POST /v1/invoke` or `GET /v1/stream`.
  Cancel follows the same shape: `POST /v1/tasks/{id}:cancel`.
- **Health is `/healthz`**, not `/health`.
- **Identity is never in a request body.** Neither `InvokeRequest` nor `TaskCreateRequest`
  accepts `user_id` or `tenant_id`; both come from the authenticated caller. Both models
  also forbid unknown fields, which is why the 422 request works.
- **Not covered here:** the `/v1/agents/{name}/live` WebSocket (Postman can open it, but it
  cannot be scripted in a newman run) and the A2A surface (`/.well-known/agents.json`,
  `/a2a/{name}`), which is empty unless an agent spec opts in with `a2a.expose: true` —
  none of the shipped examples do.
