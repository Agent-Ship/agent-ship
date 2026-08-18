"""ASGI middleware for the runtime service: security headers, then authentication.

All middleware here is written as pure-ASGI wrappers rather than
``BaseHTTPMiddleware`` so they never buffer a response body — which is what lets the
``:stream`` (SSE) and ``/live`` (WebSocket) endpoints stream event-by-event.
"""

from .auth import AuthMiddleware, get_caller, require_scope
from .headers import SecurityHeadersMiddleware

__all__ = [
    "AuthMiddleware",
    "SecurityHeadersMiddleware",
    "get_caller",
    "require_scope",
]
