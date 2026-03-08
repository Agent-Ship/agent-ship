# syntax=docker/dockerfile:1
# AgentShip Dockerfile - uses BuildKit for faster builds (cache mounts)
# Run with: DOCKER_BUILDKIT=1 docker compose build

FROM python:3.13-slim as builder

# Set working directory
WORKDIR /app

# Install system dependencies (apt cache mount speeds up repeated builds)
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    curl \
    libpq-dev \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Install pipenv (pip cache mount speeds up dependency installs)
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install pipenv

# Copy dependency files only (src not needed for install - keeps this layer cached on code changes)
COPY Pipfile Pipfile.lock pyproject.toml ./

# Install dependencies (pip cache mount - major speedup on repeated builds)
RUN --mount=type=cache,target=/root/.cache/pip \
    pipenv install --deploy --system

# Production stage
FROM python:3.13-slim

# Set working directory
WORKDIR /app

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PORT=7001

# Install runtime dependencies (including Node.js for MCP STDIO servers)
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    gnupg \
    && mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg \
    && echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_20.x nodistro main" | tee /etc/apt/sources.list.d/nodesource.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder (includes pipenv and all dependencies)
COPY --from=builder /usr/local/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Create non-root user for security (before copying files)
RUN useradd --create-home --shell /bin/bash app

# Copy application code (excludes files in .dockerignore)
COPY --chown=app:app . .

# Explicitly ensure branding folder is copied (must be after COPY . .)
# This ensures branding assets are available even if .dockerignore affects it
COPY --chown=app:app branding/ /app/branding/

# Build Sphinx documentation (if source exists and not skipped)
# Skip in local dev: docs_sphinx/build is volume-mounted in docker-compose.yml
# Set SKIP_DOCS_BUILD=false to enable (e.g. for production/Heroku builds)
ARG SKIP_DOCS_BUILD=true
RUN if [ "$SKIP_DOCS_BUILD" != "true" ] && [ -d "docs_sphinx/source" ]; then \
        cd docs_sphinx && \
        python -m sphinx -b html source build/html || echo "Warning: Sphinx build failed, /docs will show fallback page"; \
        chown -R app:app /app/docs_sphinx/build; \
    fi

USER app

# Expose port
EXPOSE 7001

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:7001/health || exit 1

# Run the application (docker-compose overrides with --port 7001)
CMD ["uvicorn", "src.service.main:app", "--host", "0.0.0.0", "--port", "7001"]

