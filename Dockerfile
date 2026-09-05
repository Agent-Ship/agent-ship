# The demo app, containerised — what a real user's AgentShip app looks like.
#
# This image is NOT the framework. AgentShip is a *dependency* here, installed like any
# other package, and this repo contributes only its own agents/ specs. That is the whole
# point of the demo: if you can build this, you can build your own app the same way.
#
# Build context is the WORKSPACE ROOT (the parent of this directory), not this directory —
# see docker-compose.yml. That is a temporary consequence of AgentShip not being on PyPI
# yet: the install below reads the sibling checkout. Once `pip install agentship` resolves
# from an index, the context becomes `.` and the COPY/install below collapses to one line:
#
#     RUN pip install "agentship-sdk[starter,observability]==0.1.0"

FROM python:3.13-slim

# curl is here for the healthcheck; nodejs is what the MCP demo agent's stdio server needs.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl nodejs npm \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# --- AgentShip, installed as a dependency -------------------------------------------------
# Extras, and why each is needed by the specs in agents/:
#   [postgres]   — the durable checkpointer behind AGENT_SESSION_STORE_URI (hitl/, triage/)
#   [mcp]        — the MCP tool client (agents/mcp/)
#   [autonomous] — deepagents, needed by agents/autonomous.yaml
# Without these, `agentship serve` rejects those specs at its doctor gate and never binds.
COPY agentship/packages/ /tmp/agentship/
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir \
        '/tmp/agentship/agentship-core[postgres]' \
        '/tmp/agentship/agentship-langgraph[postgres,mcp,autonomous]' \
        /tmp/agentship/agentship-service \
        /tmp/agentship/agentship-cli \
        /tmp/agentship/agentship-observability \
    && rm -rf /tmp/agentship

# The demo's own agents' tool dependencies. agents/deep_research.yaml and
# agents/quick_search.yaml call web_search / scrape_url, whose Firecrawl backend needs this
# package — without it the tools return {"error": "needs the 'firecrawl-py' package"} and the
# agent quietly answers from its own knowledge, which reads to a user as "I can't browse the
# web" rather than as a missing dependency.
RUN pip install --no-cache-dir 'firecrawl-py>=4'

# --- The demo app itself ------------------------------------------------------------------
# Only what the running service needs: the agent specs and the Python `code:` factories they
# reference. Tests, cassettes and demo scripts stay out of the image.
COPY agentship-demo/agents/ /app/agents/

# Run as a non-root user.
RUN useradd --create-home --shell /bin/bash demo && chown -R demo:demo /app
USER demo

# `code:` references inside the specs are resolved relative to the working directory
# (e.g. `agents/triage/agent.py:build_triage_supervisor`), so /app is where we must be.
WORKDIR /app

# Stamps which build this image is, surfaced by GET /healthz and the startup banner.
# docker-compose passes a timestamp; a CI build should pass the git sha.
ARG AGENTSHIP_BUILD=dev
ENV AGENTSHIP_BUILD=${AGENTSHIP_BUILD}

# A fallback only. Railway and docker-compose both inject PORT.
ENV PORT=7005

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS "http://localhost:${PORT}/healthz" || exit 1

# `agentship serve` validates every spec in the agents dir and builds the auth provider
# BEFORE binding a socket, so a bad spec fails the container start rather than serving a
# half-working API.
CMD ["sh", "-c", "agentship serve --host 0.0.0.0 --port ${PORT} --agents-dir ${AGENTSHIP_AGENTS_DIR:-agents} --auth ${AGENTSHIP_AUTH_PROVIDER:-api_key}"]
