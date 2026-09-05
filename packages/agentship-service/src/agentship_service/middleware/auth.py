"""Authentication middleware + the scope-authorization dependency.

:class:`AuthMiddleware` is a pure-ASGI wrapper that turns each request's credentials into a
:class:`~agentship.context.Caller` (via the configured :class:`~agentship.auth.AuthProvider`),
binds that caller and a :class:`~agentship.tenancy.TenantScope` for the request, and lets the
handler run inside them. An authentication failure is rendered as a problem+json 401/403
right here — the request never reaches a route — so no handler runs unauthenticated.

:func:`get_caller` exposes the bound caller to handlers, and :func:`require_scope` is the
per-route authorization check (``authorize(caller, agent=<path name>, verb=...)``).
"""

from __future__ import annotations

from agentship.auth import AuthProvider, authorize
from agentship.context import Caller
from agentship.errors import AuthError
from agentship.tenancy import TenantScope
from fastapi import Depends
from starlette.requests import HTTPConnection

from ..context import bind_caller, current_caller, reset_caller
from ..errors import problem_dict

#: Paths that skip authentication entirely: liveness, the API's own schema/docs, and the
#: Studio page. ``/studio`` is markup and script only — it holds no tenant data, and every
#: ``/v1`` call it makes carries the user's own key, so the data path stays authenticated.
# "/" is here for the same reason "/studio" is: it only redirects there. Without it the
# bare host answered "no API key", which is what a browser gets before it has called any
# API at all — an auth error for a navigation the user never authenticated for.
_DEFAULT_PUBLIC_PATHS = frozenset({"/", "/healthz", "/openapi.json", "/docs", "/redoc", "/studio"})


class AuthMiddleware:
    """Authenticate every request, then run it inside its caller + tenant scope."""

    def __init__(
        self, app, *, auth: AuthProvider, public_paths: frozenset[str] = _DEFAULT_PUBLIC_PATHS
    ) -> None:
        """Wrap ``app`` with the given auth provider; ``public_paths`` bypass auth."""
        self.app = app
        self._auth = auth
        self._public_paths = public_paths

    async def __call__(self, scope, receive, send) -> None:
        """Authenticate, bind identity + tenant, and dispatch — or render a 401/403."""
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        if scope["type"] == "http" and (
            scope["method"] == "OPTIONS"
            or scope["path"] in self._public_paths
            or "/.well-known/" in scope["path"]
        ):
            # CORS preflight, public endpoints, and the RFC 8615 ``.well-known`` discovery space
            # (A2A Agent Cards + the agents index) never require a credential.
            await self.app(scope, receive, send)
            return

        # HTTPConnection is the shared base of Request and WebSocket; the auth providers
        # only read headers, so it serves both an HTTP request and a WS handshake.
        connection = HTTPConnection(scope)
        try:
            caller = await self._auth.authenticate(connection)
        except AuthError as exc:
            await self._reject(scope, receive, send, exc)
            return

        caller_token = bind_caller(caller)
        try:
            with TenantScope(caller):
                await self.app(scope, receive, send)
        finally:
            reset_caller(caller_token)

    async def _reject(self, scope, receive, send, exc: AuthError) -> None:
        """Render an authentication failure as an ASGI problem+json response."""
        if scope["type"] == "websocket":
            # For a WebSocket handshake, refuse to accept and close with a policy code.
            await send({"type": "websocket.close", "code": 4401})
            return
        status = 403 if exc.code == "forbidden" else 401
        title = "Forbidden" if status == 403 else "Unauthorized"
        body = problem_dict(status=status, title=title, code=exc.code, detail=str(exc))
        await _send_json(send, status, body)


async def _send_json(send, status: int, body: dict) -> None:
    """Send a one-shot ``application/problem+json`` response over raw ASGI."""
    import json

    payload = json.dumps(body).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/problem+json"),
                (b"content-length", str(len(payload)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": payload})


def get_caller() -> Caller:
    """FastAPI dependency: the authenticated caller bound for this request."""
    return current_caller()


def require_scope(verb: str):
    """Build a dependency that authorizes ``verb`` on the agent named in the path.

    Used on the agent routes (``/v1/agents/{name}:invoke`` etc.): it reads the ``name``
    path parameter and the bound caller, and raises ``AuthError('forbidden')`` (→ 403)
    unless the caller holds a scope granting ``agent:{name}:{verb}``. Returns the caller so
    a handler can depend on it directly.
    """

    def dependency(name: str, caller: Caller = Depends(get_caller)) -> Caller:
        authorize(caller, agent=name, verb=verb)
        return caller

    return dependency
