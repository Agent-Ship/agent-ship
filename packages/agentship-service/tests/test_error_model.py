"""The RFC-9457 error contract: each harness error maps to its problem+json response.

Rather than unit-test each handler function, these mount a route that raises the error and
assert the wire response, proving the handler is registered *and* renders the right
status/code/media-type with a trace id and no leaked internals.
"""

from __future__ import annotations

import pytest
from agentship.errors import (
    AgentShipError,
    CapabilityError,
    ModelError,
    ResumeError,
    TenantViolation,
    ThreadBusyError,
)
from agentship_service.errors import install_error_handlers
from agentship_service.middleware import SecurityHeadersMiddleware
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _app_that_raises(exc: Exception) -> FastAPI:
    """Build a minimal app whose routes raise ``exc`` (GET and POST), with error handlers."""
    app = FastAPI()
    install_error_handlers(app)
    # SecurityHeaders binds a trace id so the problem body can echo it.
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get("/boom")
    async def boom_get():  # noqa: ANN202
        raise exc

    @app.post("/boom")
    async def boom_post():  # noqa: ANN202
        raise exc

    return app


def _client_for(exc: Exception) -> TestClient:
    """A ``TestClient`` over an app whose routes raise ``exc`` (server errors not re-raised)."""
    return TestClient(_app_that_raises(exc), raise_server_exceptions=False)


@pytest.mark.parametrize(
    ("exc", "status", "code"),
    [
        (ThreadBusyError("busy"), 409, "thread_busy"),
        (ResumeError("stale"), 409, "resume_failed"),
        (CapabilityError("no stream"), 400, "unsupported"),
        (ModelError("provider down"), 502, "model_error"),
        (AgentShipError("misconfig"), 500, "internal_error"),
    ],
)
def test_error_maps_to_problem_json(exc: Exception, status: int, code: str) -> None:
    """Each known harness error renders as problem+json with its status/code and a trace id."""
    client = _client_for(exc)
    resp = client.get("/boom")
    assert resp.status_code == status
    assert resp.headers["content-type"] == "application/problem+json"
    body = resp.json()
    assert body["status"] == status
    assert body["code"] == code
    assert body["trace_id"]


def test_tenant_violation_is_404_on_read() -> None:
    """A cross-tenant read is hidden as 404 so a client cannot learn the resource exists."""
    client = _client_for(TenantViolation("t2", "t1"))
    resp = client.get("/boom")
    assert resp.status_code == 404
    assert resp.json()["code"] == "not_found"


def test_tenant_violation_is_403_on_write() -> None:
    """A cross-tenant write is an explicit 403 Forbidden."""
    client = _client_for(TenantViolation("t2", "t1"))
    resp = client.post("/boom")
    assert resp.status_code == 403
    assert resp.json()["code"] == "forbidden"


def test_unhandled_error_is_generic_500_without_leak() -> None:
    """An unexpected error → generic 500 whose body never contains the exception text."""
    client = _client_for(RuntimeError("secret internal detail"))
    resp = client.get("/boom")
    assert resp.status_code == 500
    body = resp.json()
    assert body["code"] == "internal_error"
    assert "secret internal detail" not in resp.text


def test_an_unexpected_500_is_as_hardened_as_every_other_response() -> None:
    """A 500 must carry the same security headers and a trace id as a 200 does.

    Starlette's own backstop, ``ServerErrorMiddleware``, is the OUTERMOST layer — outside
    anything the app mounts. A 500 it rendered therefore never travelled back out through
    the security-headers middleware, and that middleware's ``finally`` had already reset
    the trace-id contextvar before the body was built. So the one response a caller is
    most likely to report was the one with no headers and ``trace_id: null``.
    """
    from agentship_service import create_app
    from agentship_service.registry import AgentRegistry

    class _Boom:
        """An auth provider whose authenticate blows up in an unforeseen way."""

        async def authenticate(self, request):
            """Fail with an error nothing has a handler for."""
            raise RuntimeError("unforeseen")

    client = TestClient(
        create_app(auth=_Boom(), agents=AgentRegistry([])), raise_server_exceptions=False
    )
    resp = client.get("/v1/agents", headers={"x-api-key": "whatever"})

    assert resp.status_code == 500
    assert resp.headers["content-type"] == "application/problem+json"
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "DENY"
    assert resp.headers["referrer-policy"] == "no-referrer"
    assert resp.headers["x-trace-id"], "a 500 is exactly when a caller needs the trace id"
    body = resp.json()
    assert body["code"] == "internal_error"
    assert body["trace_id"] == resp.headers["x-trace-id"], "header and body must agree"
    assert "unforeseen" not in resp.text, "still no leak"
