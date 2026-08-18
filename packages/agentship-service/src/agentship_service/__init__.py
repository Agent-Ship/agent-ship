"""AgentShip runtime service: the REST / SSE / WebSocket surface over an agent.

This package is the irreducible in-app core a gateway cannot replace: it authenticates
each request into a :class:`~agentship.context.Caller`, binds a tenant scope so every
query is filtered to the caller's tenant, and renders every failure as RFC-9457
problem+json. :func:`create_app` assembles the FastAPI app and its middleware stack.
"""

from __future__ import annotations

from .app import create_app

__all__ = ["create_app"]
