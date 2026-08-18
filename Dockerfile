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

# Install the packages. Copy only pyprojects first so the dependency layer caches across
# source edits, then the sources.
COPY packages/agentship-core/pyproject.toml packages/agentship-core/
COPY packages/agentship-langgraph/pyproject.toml packages/agentship-langgraph/
COPY packages/agentship-service/pyproject.toml packages/agentship-service/
COPY packages/agentship-cli/pyproject.toml packages/agentship-cli/
COPY packages/ packages/
COPY examples/ agents/

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --upgrade pip \
    && pip install \
        ./packages/agentship-core \
        ./packages/agentship-langgraph \
        ./packages/agentship-service \
        ./packages/agentship-cli

# Run as a non-root user.
RUN useradd --create-home --shell /bin/bash app && chown -R app:app /app
USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD curl -f "http://localhost:${PORT}/healthz" || exit 1

# `agentship serve` doctor-gates the agents dir + auth provider before binding. Bind 0.0.0.0
# so the container is reachable; auth is required on every /v1 route regardless of bind.
CMD ["sh", "-c", "agentship serve --host 0.0.0.0 --port ${PORT} --agents-dir ${AGENTSHIP_AGENTS_DIR} --auth ${AGENTSHIP_AUTH_PROVIDER}"]
