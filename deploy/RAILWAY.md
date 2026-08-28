# Deploying the AgentShip service to Railway

Railway builds the repo-root `Dockerfile` and runs the image. The image starts
`agentship serve`, which validates every spec in `/app/agents` and builds the auth
provider **before** it binds a socket — a bad spec or a missing auth config fails the
deploy instead of shipping a half-working service.

Everything below is run by you. Nothing here creates remote resources on its own.

---

## Prerequisites

- A Railway account and the Railway CLI (`brew install railway`), logged in: `railway login`.
- Docker locally, if you want to check the image builds before pushing (`make docker-build`).
- At least one model provider key (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, …) for the
  agents you intend to serve.

## About `railway.json` — read this before you rely on it

`railway.json` at the repo root declares the builder (`DOCKERFILE`), the start command,
the healthcheck path (`/healthz`), and the restart policy.

**It may not be read.** Railway's Config-as-Code is deprecated: per
[Railway's docs](https://docs.railway.com/infrastructure-as-code) the files "keep working
for legacy services until 2026-12-01", but **new services cannot opt into Config as
Code**. So on a service you create today, `railway.json` is likely ignored.

Nothing breaks either way. Railway auto-detects the root `Dockerfile` and runs its `CMD`,
which is identical to the `startCommand` in `railway.json` — so build and start are
correct with or without the file. The two settings that would be lost are the healthcheck
path and the restart policy. **Set those in the service settings (step 5 below)** and
treat `railway.json` as documentation of intent rather than the mechanism.

The supported replacement is `.railway/railway.ts` Infrastructure-as-Code. It needs a
newer CLI than 5.1.0 (which has no `railway config` subcommand) and is in early access.
When you have access, that is the right place to codify the service, the database, and
the variable mapping below — and a service cannot be managed by both systems, so delete
`railway.json` when you migrate.

---

## Environment variables

Set these on the **service**, not the database. Values are yours to fill in — never
commit them.

**Required**

| Variable | What it is |
|---|---|
| `AGENTSHIP_API_KEYS` | JSON array of API keys the service accepts, e.g. `[{"key":"…","user":"…","tenant":"…","scopes":["*"]}]`. Every `/v1` route requires one; `/healthz` does not. |
| One provider key | `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `GEMINI_API_KEY` / … — whichever your agents' `model:` strings resolve to. |

**Postgres — the mapping you must wire explicitly**

Railway's managed Postgres publishes `DATABASE_URL`. AgentShip reads a *different* name,
so point one at the other with a Railway reference variable:

| Variable | Set to | Why |
|---|---|---|
| `AGENT_SESSION_STORE_URI` | `${{Postgres.DATABASE_URL}}` | The LangGraph Postgres checkpointer. Without it the service still runs, but on an in-memory saver — runs are not durable and cannot be resumed after a restart. |
| `AGENTSHIP_DATABASE_URL` | `${{Postgres.DATABASE_URL}}` | Only used by `agentship db upgrade`. Optional: that command already falls back to a plain `DATABASE_URL`, which Railway injects if you attach the database to the service. |

Replace `Postgres` with your database service's actual name if you renamed it.

**Optional**

| Variable | Default | What it does |
|---|---|---|
| `AGENTSHIP_AGENTS_DIR` | `/app/agents` | Directory of specs to serve. The image copies `examples/` here. |
| `AGENTSHIP_AUTH_PROVIDER` | `api_key` | `api_key` \| `forwarded` \| `jwt` \| `composite`. Use `forwarded` only behind a gateway you trust, together with `AGENTSHIP_TRUST_FORWARDED_FROM`. |
| `AGENTSHIP_CORS_ORIGINS` | none | Comma-separated allowed origins. |
| `AGENTSHIP_HSTS` | off | `1` to send HSTS. Railway already terminates TLS. |
| `AGENTSHIP_RATE_LIMIT` | off | `1` for the in-app limiter. Prefer rate limiting at the edge. |
| `AGENTSHIP_HASH_SALT` | empty | Per-deployment salt for the hashed user id in traces. Set it so ids cannot be correlated across deployments. |
| `AGENTSHIP_SERVICE_NAME` | `agentship` | `service.name` on emitted OTel spans. |
| `WEB_CONCURRENCY` | 1 | Worker processes, if you switch to the `Procfile` start command. |

`PORT` is injected by Railway. Do not set it — the image binds `0.0.0.0:$PORT`.

---

## Deploy

```bash
cd agentship

# 1. Create the project and link this directory to it.
railway init
railway link

# 2. Add managed Postgres.
railway add --database postgres

# 3. Set the service variables (KEY=VALUE, quoted so the shell leaves them alone).
railway variable set 'AGENTSHIP_API_KEYS=[{"key":"CHANGE-ME","user":"you","tenant":"prod","scopes":["*"]}]'
railway variable set 'OPENAI_API_KEY=…'
railway variable set 'AGENT_SESSION_STORE_URI=${{Postgres.DATABASE_URL}}'

# 4. Build and deploy from this directory.
railway up

# 5. Give the service a public URL.
railway domain
```

In the service's **Settings → Deploy**, set (these mirror `railway.json`, which a new
service will probably ignore — see the section above):

- **Healthcheck path**: `/healthz`
- **Restart policy**: `ON_FAILURE`

Railway then waits for the app to answer `/healthz` before shifting traffic to a new
deployment, instead of cutting over to a container that is still starting.

`make railway-deploy` runs step 4 for you once the project is linked.

---

## Verify it is healthy

```bash
URL=https://<your-service>.up.railway.app

# Public liveness probe — expect 200.
curl -i $URL/healthz

# The /v1 surface requires a key — expect 401 without one.
curl -o /dev/null -w '%{http_code}\n' $URL/v1/agents

# With a key — expect the list of served agents.
curl -H "Authorization: Bearer CHANGE-ME" $URL/v1/agents
```

Then confirm the deploy log's first two lines, which report what the doctor gate let
through:

```bash
railway logs
# Serving 8 agent(s) from /app/agents on http://0.0.0.0:8080
# Auth provider: api_key
```

If the agent count is lower than expected, a spec was rejected — the reason is printed
above those lines as `✗ <file>: <reason>`.

---

## Database migrations

`agentship db upgrade` is the only sanctioned migration runner, and it is plan-only
unless you pass `--allow-migrations`. There are no registered migrations today, so a
fresh deploy needs no DDL. When one lands:

```bash
railway run agentship db upgrade                      # plan only, touches nothing
railway run agentship db upgrade --allow-migrations   # applies
```

Run it deliberately. Do not wire it into the container start command.

---

## Troubleshooting

**Deploy crash-loops immediately, log ends with `doctor gate failed`.** A spec in
`AGENTSHIP_AGENTS_DIR` is invalid or needs an engine extra that is not installed. The
`✗ <file>: <reason>` lines name each one.

**`Error: … auth provider`, no server line.** The auth provider could not be built —
usually `AGENTSHIP_API_KEYS` is missing or is not valid JSON, or `forwarded` was selected
without `AGENTSHIP_TRUST_FORWARDED_FROM`.

**Healthcheck times out.** Confirm the healthcheck path is `/healthz` (not `/health`) and
that nothing has overridden `PORT`.

**Runs restart from scratch after a redeploy.** `AGENT_SESSION_STORE_URI` is unset, so
the service is on the in-memory saver. Check the reference variable resolved to a real
DSN: `railway variable list`.

## Checking locally first

`make docker-up` runs the same image plus a Postgres container wired to
`AGENT_SESSION_STORE_URI`, which is the closest local mirror of the Railway setup:

```bash
make docker-up
curl -i http://localhost:8000/healthz
curl -H "Authorization: Bearer dev" http://localhost:8000/v1/agents
make docker-down
```
