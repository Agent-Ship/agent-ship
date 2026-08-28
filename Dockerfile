# syntax=docker/dockerfile:1
# AgentShip runtime-service image. Installs the monorepo packages and serves the agents in
# ./agents over the secure /v1 surface via `agentship serve` (the doctor-gated launch path).
#
#   DOCKER_BUILDKIT=1 docker build -t agentship .
#   docker run -p 8000:8000 -e AGENTSHIP_API_KEYS='[{"key":"dev","user":"dev","tenant":"dev","scopes":["*"]}]' agentship
#
# Node.js is included so STDIO MCP servers (npx-launched) work inside the container.

FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000 \
    AGENTSHIP_AGENTS_DIR=/app/agents \
    AGENTSHIP_AUTH_PROVIDER=api_key

WORKDIR /app

# Runtime deps: curl for the healthcheck, Node.js 20 for STDIO MCP servers.
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates gnupg \
    && mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
        | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg \
    && echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_20.x nodistro main" \
        > /etc/apt/sources.list.d/nodesource.list \
    && apt-get update && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# The packages, plus the example agents the image serves by default.
COPY packages/ packages/
COPY examples/ agents/

# Install every package in one resolver pass so the sibling `agentship-core` requirements
# resolve to these local copies instead of PyPI. The extras are the ones the shipped
# agents/ specs and a real deployment need:
#   core[postgres] + langgraph[postgres] — the Postgres checkpointer behind
#       AGENT_SESSION_STORE_URI, so runs are crash-durable and resumable.
#   langgraph[mcp]                       — the MCP tool client (this is what the Node.js
#       install above exists for).
#   langgraph[autonomous]                — deepagents, needed by agents/autonomous.yaml;
#       without it `serve`'s doctor gate rejects that spec and the container never binds.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --upgrade pip \
    && pip install \
        './packages/agentship-core[postgres]' \
        './packages/agentship-langgraph[postgres,mcp,autonomous]' \
        ./packages/agentship-service \
        ./packages/agentship-cli \
        ./packages/agentship-observability

# Run as a non-root user.
RUN useradd --create-home --shell /bin/bash app && chown -R app:app /app
USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD curl -f "http://localhost:${PORT}/healthz" || exit 1

# `agentship serve` doctor-gates the agents dir + auth provider before binding. Bind 0.0.0.0
# so the container is reachable; auth is required on every /v1 route regardless of bind.
CMD ["sh", "-c", "agentship serve --host 0.0.0.0 --port ${PORT} --agents-dir ${AGENTSHIP_AGENTS_DIR} --auth ${AGENTSHIP_AUTH_PROVIDER}"]
