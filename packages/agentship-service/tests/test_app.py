"""``create_app`` wiring: middleware order, public bypass, and the problem+json contract.

These tests exercise the assembled app end to end through Starlette's ``TestClient`` — a
real ASGI round-trip — rather than calling the middleware in isolation, so they prove the
layers compose in the documented order (CORS → SecurityHeaders → Auth → router) and that an
auth failure is rendered as RFC-9457 before any route runs.
"""

from __future__ import annotations

import pytest
from agentship.auth import ApiKeyAuthProvider, EnvApiKeyStore
from agentship_service import create_app
from agentship_service.middleware import (
    AuthMiddleware,
    RateLimitMiddleware,
    SecurityHeadersMiddleware,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.cors import CORSMiddleware

#: A single API key ("secret") mapped to user ``u1`` in tenant ``t1``.
_KEY_TABLE = '[{"key": "secret", "user": "u1", "tenant": "t1", "scopes": ["*"]}]'


def _auth() -> ApiKeyAuthProvider:
    """Build an API-key auth provider backed by the one-row test table."""
    return ApiKeyAuthProvider(EnvApiKeyStore(raw=_KEY_TABLE))


def _app(**kwargs) -> FastAPI:
    """Build the service app with a probe route that echoes the bound caller."""
    app = create_app(auth=_auth(), **kwargs)

    @app.get("/v1/whoami")
    async def whoami():  # noqa: ANN202 — test-only probe
        from agentship_service.middleware import get_caller

        caller = get_caller()
        return {"user": caller.user_id, "tenant": caller.tenant_id}

    return app


def test_healthz_is_public() -> None:
    """The liveness probe needs no credential and reports ok."""
    client = TestClient(_app())
    resp = client.get("/healthz")
    assert resp.status_code == 200
    # Asserts the status field, not the whole body: the probe also carries build info, and
    # adding a field to a health response must not break a caller (or this test).
    assert resp.json()["status"] == "ok"


def test_missing_credential_is_problem_json_401() -> None:
    """A protected route with no credential → 401 problem+json, never reaching the handler."""
    client = TestClient(_app())
    resp = client.get("/v1/whoami")
    assert resp.status_code == 401
    assert resp.headers["content-type"] == "application/problem+json"
    body = resp.json()
    assert body["status"] == 401
    assert body["code"] == "no_credentials"
    # The trace id was minted by the outer SecurityHeaders layer before auth ran.
    assert body["trace_id"]
    assert body["trace_id"] == resp.headers["x-trace-id"]


def test_valid_credential_binds_caller() -> None:
    """A valid key authenticates and the handler sees the bound caller's identity."""
    client = TestClient(_app())
    resp = client.get("/v1/whoami", headers={"x-api-key": "secret"})
    assert resp.status_code == 200
    assert resp.json() == {"user": "u1", "tenant": "t1"}


def test_bad_credential_is_401_invalid_api_key() -> None:
    """A present-but-wrong key is recognized-and-rejected → 401 ``invalid_api_key``."""
    client = TestClient(_app())
    resp = client.get("/v1/whoami", headers={"x-api-key": "wrong"})
    assert resp.status_code == 401
    assert resp.json()["code"] == "invalid_api_key"


def test_security_headers_on_every_response() -> None:
    """The hardening headers are stamped even on an unauthenticated 401."""
    client = TestClient(_app())
    resp = client.get("/v1/whoami")
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "DENY"
    assert resp.headers["referrer-policy"] == "no-referrer"
    assert "strict-transport-security" not in resp.headers


def test_hsts_opt_in() -> None:
    """``hsts=True`` adds Strict-Transport-Security; it is off by default."""
    client = TestClient(_app(hsts=True))
    resp = client.get("/healthz")
    assert "strict-transport-security" in resp.headers


def test_middleware_order_is_cors_then_headers_then_ratelimit_then_auth() -> None:
    """The stack is mounted outermost→innermost: CORS, SecurityHeaders, RateLimit, Auth.

    Starlette lists ``user_middleware`` outermost-first (index 0 = added last), so the
    documented request path CORS → SecurityHeaders → RateLimit → Auth → router is exactly
    this order.
    """
    app = create_app(auth=_auth(), cors_origins=["https://app.example"])
    classes = [m.cls for m in app.user_middleware]
    assert classes == [
        CORSMiddleware,
        SecurityHeadersMiddleware,
        RateLimitMiddleware,
        AuthMiddleware,
    ]


def test_cors_absent_when_no_origins() -> None:
    """With no allow-list, CORS is not mounted — only SecurityHeaders, RateLimit, Auth."""
    app = create_app(auth=_auth())
    classes = [m.cls for m in app.user_middleware]
    assert classes == [SecurityHeadersMiddleware, RateLimitMiddleware, AuthMiddleware]


def test_options_preflight_short_circuits_before_auth() -> None:
    """A CORS preflight is answered by the CORS layer and never hits auth (no 401)."""
    client = TestClient(_app(cors_origins=["https://app.example"]))
    resp = client.options(
        "/v1/whoami",
        headers={
            "origin": "https://app.example",
            "access-control-request-method": "GET",
        },
    )
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == "https://app.example"


def test_trace_id_adopts_inbound_request_id() -> None:
    """An inbound ``x-request-id`` is adopted as the trace id rather than a fresh one."""
    client = TestClient(_app())
    resp = client.get("/healthz", headers={"x-request-id": "req-123"})
    assert resp.headers["x-trace-id"] == "req-123"


@pytest.mark.parametrize("path", ["/openapi.json", "/docs", "/redoc"])
def test_schema_and_docs_are_public(path: str) -> None:
    """The API's own schema and docs bypass auth so tooling can introspect it."""
    client = TestClient(_app())
    resp = client.get(path)
    assert resp.status_code == 200


def test_healthz_reports_which_build_is_answering() -> None:
    """``/healthz`` carries the build stamp and package versions, not just ``ok``.

    A deployment that cannot say what code it is running is one you cannot trust a bug
    report against. This is the check that answers "is the container actually on the
    latest framework?" from outside, without shelling in.
    """
    from agentship.auth import ApiKeyAuthProvider, EnvApiKeyStore
    from agentship_service import AgentRegistry, create_app
    from fastapi.testclient import TestClient

    auth = ApiKeyAuthProvider(EnvApiKeyStore(raw="[]"))
    client = TestClient(create_app(auth=auth, agents=AgentRegistry()))

    body = client.get("/healthz").json()
    assert body["status"] == "ok"
    assert body["build"]  # "dev" locally, a git sha in a built image
    assert "agentship-core" in body["packages"]
