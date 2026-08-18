"""The v1 HTTP/SSE/WS routers: agent invocation + discovery, and the task skeleton.

Each router is mounted by :func:`~agentship_service.app.create_app`. They read the
per-request caller and tenant bound by the auth middleware, so a route never re-derives
identity — it authorizes and scopes against what is already proven.
"""

from .agents import router as agents_router
from .live import router as live_router
from .tasks import router as tasks_router

__all__ = ["agents_router", "live_router", "tasks_router"]
