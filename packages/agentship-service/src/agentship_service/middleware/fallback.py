"""Turn an unhandled exception into a problem+json response *inside* the middleware stack.

Starlette's own backstop, ``ServerErrorMiddleware``, is the OUTERMOST layer — outside
everything an app mounts. So a 500 it renders never passes back through
:class:`~agentship_service.middleware.headers.SecurityHeadersMiddleware`: the security
headers are never stamped, and the trace id has already been reset by that middleware's
``finally`` before the handler runs. The result was a 500 that was measurably weaker than
every other response the service sends — no ``x-content-type-options``, no
``x-frame-options``, no ``referrer-policy``, and ``trace_id: null`` in the body, which is
exactly the response someone needs a trace id for.

Catching the exception one layer further IN fixes both at once: the response is an ordinary
response travelling back out through the stack, so it is stamped like any other and the
trace-id contextvar is still bound when the body is built.

This does not replace the error handlers in :mod:`agentship_service.errors`. Everything
they register is handled by ``ExceptionMiddleware`` and never reaches here; only genuinely
unexpected exceptions do.
"""

from __future__ import annotations

import logging

from ..errors import PROBLEM_JSON, problem_dict

logger = logging.getLogger("agentship.service")


class ProblemFallbackMiddleware:
    """Render any exception that escapes the routes as problem+json, in-stack."""

    def __init__(self, app) -> None:
        """Wrap ``app``; must be mounted INSIDE the security-headers middleware."""
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        """Run the app, converting an escaped exception into a 500 problem document."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = False

        async def watch(message: dict) -> None:
            """Forward messages, remembering whether the response has begun."""
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, watch)
        except Exception:
            # Once the status line is on the wire there is no status left to change — a
            # streaming response must signal failure in its own frames (the SSE route emits
            # a terminal `error` frame). Re-raise so Starlette closes the connection.
            if started:
                raise
            logger.exception("unhandled error serving %s", scope.get("path", "?"))
            await _send_problem(send)


async def _send_problem(send) -> None:
    """Send a generic 500 problem document, never echoing the exception text."""
    import json

    body = json.dumps(
        problem_dict(status=500, title="Internal Server Error", code="internal_error")
    ).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 500,
            "headers": [
                (b"content-type", PROBLEM_JSON.encode()),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
