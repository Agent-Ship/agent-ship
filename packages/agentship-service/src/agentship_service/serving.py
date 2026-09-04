"""Build a runtime-service app from environment configuration — the ``agentship serve`` factory.

``agentship serve`` doctor-gates and then launches uvicorn. Under ``--reload`` or
``--workers``, uvicorn re-imports the app in worker subprocesses, so the app must be
constructible from an import string with no arguments. :func:`build_from_env` is that
factory: it reads the agents directory, the auth provider, and the HTTP-posture switches
from environment variables (which ``serve`` sets in the parent before spawning), loads every
``agents/*.yaml`` into an :class:`AgentRegistry`, and returns the assembled app.

Keeping the whole configuration in the environment (rather than a pickled app) is what lets
a single factory serve the single-process, ``--reload``, and multi-``--workers`` paths
identically.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from agentship.auth.registry import build_auth_provider
from agentship.runtime import build_agent
from fastapi import FastAPI

from .app import create_app
from .registry import AgentRegistry

#: Environment variables the factory reads. ``serve`` sets these; they are also the
#: documented way to configure the app when launched by an external ASGI server.
ENV_AGENTS_DIR = "AGENTSHIP_AGENTS_DIR"
ENV_AUTH_PROVIDER = "AGENTSHIP_AUTH_PROVIDER"
ENV_CORS_ORIGINS = "AGENTSHIP_CORS_ORIGINS"
ENV_HSTS = "AGENTSHIP_HSTS"
ENV_RATE_LIMIT = "AGENTSHIP_RATE_LIMIT"
ENV_TRUST_FORWARDED_FROM = "AGENTSHIP_TRUST_FORWARDED_FROM"


#: Startup failures are reported here: which spec could not be built, and why.
_log = logging.getLogger("agentship.service")


def load_agents(agents_dir: Path) -> AgentRegistry:
    """Build every ``*.yaml``/``*.yml`` spec directly under ``agents_dir`` into a registry.

    Only the directory's own specs are loaded (not a deep walk), matching ``agentship
    doctor``'s scoping so a nested Python fixture folder is never mistaken for a spec. A
    missing directory yields an empty registry — the service still boots and serves no
    agents, which is a valid (if idle) state.
    """
    registry = AgentRegistry()
    if not agents_dir.is_dir():
        return registry
    for path in sorted(p for p in agents_dir.iterdir() if p.suffix in (".yaml", ".yml")):
        try:
            registry.add(build_agent(str(path)))
        except Exception:  # noqa: BLE001 - one bad spec must not cost us the other agents
            # Building happens at startup, so an exception here used to escape the app factory:
            # uvicorn never bound a port and EVERY agent became unreachable because one of them
            # could not reach its MCP server. A deployment is more useful degraded than dead —
            # the failure is logged with its traceback and the agent is left out of the
            # catalogue, so `GET /v1/agents` shows exactly what is servable.
            _log.exception("agent %s could not be built and will not be served", path.name)
    return registry


def build_from_env() -> FastAPI:
    """Assemble the service app from environment variables (the ``agentship serve`` factory)."""
    agents_dir = Path(os.environ.get(ENV_AGENTS_DIR, "agents"))
    auth_name = os.environ.get(ENV_AUTH_PROVIDER, "api_key")
    auth = build_auth_provider(auth_name, _auth_config_from_env(auth_name))
    return create_app(
        auth=auth,
        agents=load_agents(agents_dir),
        cors_origins=_split_csv(os.environ.get(ENV_CORS_ORIGINS)),
        hsts=_env_flag(ENV_HSTS),
        rate_limit=_env_flag(ENV_RATE_LIMIT),
    )


def _auth_config_from_env(auth_name: str) -> dict:
    """Assemble the selected provider's config from environment variables.

    Only the forwarded-header provider needs config here (its trusted-source allow-list);
    the API-key and JWT providers read their own dedicated environment variables inside
    their factories, so an empty config is correct for them.
    """
    if auth_name == "forwarded":
        return {"trust_forwarded_from": _split_csv(os.environ.get(ENV_TRUST_FORWARDED_FROM))}
    return {}


def _split_csv(raw: str | None) -> tuple[str, ...]:
    """Split a comma-separated environment value into a tuple, dropping blanks."""
    if not raw:
        return ()
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def _env_flag(name: str) -> bool:
    """Read a boolean environment flag (``1``/``true``/``yes``/``on`` → True)."""
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")
