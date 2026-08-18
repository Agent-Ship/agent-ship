"""Security headers + per-request trace id — the outermost app-owned middleware.

A pure-ASGI wrapper that (1) mints a trace id for the request (adopting an inbound
``x-request-id`` when present) and binds it so auth-failure bodies and handlers can echo
it, and (2) stamps a small set of hardening headers plus ``x-trace-id`` onto every
response. Being outermost, the trace id is bound before authentication runs, so even a
401/403 carries it. HSTS is opt-in (``hsts=True``) because it should only be sent when the
service is genuinely served over TLS.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

from ..context import bind_trace_id, reset_trace_id

#: Hardening headers sent on every response. Conservative for a JSON/stream API: refuse
#: content-type sniffing, framing, and referrer leakage.
_BASE_SECURITY_HEADERS = {
    b"x-content-type-options": b"nosniff",
    b"x-frame-options": b"DENY",
    b"referrer-policy": b"no-referrer",
}


class SecurityHeadersMiddleware:
    """Bind a trace id and add security headers to every response."""

    def __init__(self, app, *, hsts: bool = False) -> None:
        """Wrap ``app``; set ``hsts=True`` to also send Strict-Transport-Security (TLS only)."""
        self.app = app
        self._extra = dict(_BASE_SECURITY_HEADERS)
        if hsts:
            self._extra[b"strict-transport-security"] = b"max-age=63072000; includeSubDomains"

    async def __call__(self, scope, receive, send) -> None:
        """Mint+bind the trace id, then stream the response with headers added."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        trace_id = _incoming_request_id(scope) or uuid.uuid4().hex
        token = bind_trace_id(trace_id)
        try:
            await self.app(scope, receive, _stamp(send, self._extra, trace_id))
        finally:
            reset_trace_id(token)


def _stamp(send: Callable[[dict], Awaitable[None]], extra: dict[bytes, bytes], trace_id: str):
    """Return a ``send`` wrapper that adds the security + trace headers to the response start."""

    async def wrapped(message: dict) -> None:
        if message["type"] == "http.response.start":
            headers = list(message.get("headers", []))
            existing = {name for name, _ in headers}
            for name, value in extra.items():
                if name not in existing:
                    headers.append((name, value))
            headers.append((b"x-trace-id", trace_id.encode("ascii")))
            message = {**message, "headers": headers}
        await send(message)

    return wrapped


def _incoming_request_id(scope) -> str | None:
    """Return the inbound ``x-request-id`` header value, if a proxy already set one."""
    for name, value in scope.get("headers", []):
        if name == b"x-request-id":
            return value.decode("latin-1")
    return None
