"""Demo slice (Phase 09): a served AgentShip app is safe by default.

"Safe by default" means you get the hardening without configuring it, and the one switch
that could be wrong in the wrong environment (HSTS) is off until you say otherwise. This
slice pins four things a deployment reviewer would check first:

* the hardening response headers are on every response, including error responses;
* HSTS is opt-in, because promising TLS from a service served over plain HTTP breaks it;
* CORS is an explicit allow-list — an origin that is not on it is refused;
* only a trusted front door may assert a caller's identity.

One honest note on the last point. The phase spec describes a loopback-only ``/api/*``
dev router that refuses a non-loopback caller. **This rebuild ships no such router** —
there is no dev ingress, so there is nothing to lock down, and the test below asserts
that absence rather than pretending to exercise a route that does not exist. The
guarantee that *does* ship in its place is the forwarded-header provider's trusted-source
allow-list, which is exercised for real.

Keyless and offline.

    pytest tests/test_posture_demo.py
"""

from __future__ import annotations

from agentship.auth import ForwardedHeaderAuthProvider
from agentship.runtime import build_agent
from agentship_cli.main import serve
from agentship_service import AgentRegistry, create_app
from conftest import ACME, ALLOWED_ORIGIN, SERVICE_AGENT
from fastapi.testclient import TestClient

#: Sent on every response regardless of status: no content-type sniffing, no framing,
#: no referrer leakage.
EXPECTED_HEADERS = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
}

#: The gateway this deployment trusts to forward already-verified identity.
TRUSTED_GATEWAY = "agentgateway"


def test_security_headers_are_on_a_successful_response(service_client):
    """A 200 carries the hardening headers plus the trace id, with nothing to configure."""
    response = service_client.post(
        "/v1/agents/support:invoke", headers=ACME, json={"input": "hello"}
    )

    assert response.status_code == 200
    for name, value in EXPECTED_HEADERS.items():
        assert response.headers[name] == value
    assert response.headers["x-trace-id"]


def test_security_headers_are_on_an_error_response_too(service_client):
    """A 401 carries the same headers — the hardening does not depend on reaching a route.

    This is why the header middleware sits outside authentication: an error path is
    exactly where a forgotten header would go unnoticed.
    """
    response = service_client.post("/v1/agents/support:invoke", json={"input": "hello"})

    assert response.status_code == 401
    for name, value in EXPECTED_HEADERS.items():
        assert response.headers[name] == value
    assert response.headers["x-trace-id"]


def test_hsts_is_off_unless_you_ask_for_it():
    """Strict-Transport-Security is absent by default and present when enabled.

    It is opt-in because a browser that sees HSTS pins the host to HTTPS for years. Sent
    from a service reachable over plain HTTP — a local run, a plain-HTTP internal
    deployment — that is a self-inflicted outage, so it ships off.
    """
    default = TestClient(create_app(auth=_trusting_gateway_auth()))
    over_tls = TestClient(create_app(auth=_trusting_gateway_auth(), hsts=True))

    assert "strict-transport-security" not in default.get("/healthz").headers
    assert "max-age=" in over_tls.get("/healthz").headers["strict-transport-security"]


def test_a_disallowed_cors_origin_is_refused_at_the_preflight(service_client):
    """A browser preflight from an unlisted origin is refused before it reaches the route.

    Refused at the outermost layer, so the request never touches authentication or an
    agent — the browser is told "no" and stops.
    """
    denied = service_client.options(
        "/v1/agents/support:invoke",
        headers={"origin": "https://evil.example", "access-control-request-method": "POST"},
    )

    assert denied.status_code == 400
    assert "access-control-allow-origin" not in denied.headers


def test_the_allowed_cors_origin_is_let_through(service_client):
    """The one configured origin preflights fine, so the allow-list is a list, not a wall."""
    allowed = service_client.options(
        "/v1/agents/support:invoke",
        headers={"origin": ALLOWED_ORIGIN, "access-control-request-method": "POST"},
    )

    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == ALLOWED_ORIGIN


def test_a_disallowed_origin_gets_no_permission_to_read_the_response(service_client):
    """A simple cross-origin POST is answered, but without the header that lets JS read it.

    Worth stating plainly, because the status code is misleading: a non-preflighted
    request still runs (it had a valid credential). What CORS withholds is
    ``access-control-allow-origin``, so the calling page cannot read the body. CORS
    protects the browser's users; it is not an access-control mechanism, which is why the
    credential check in test_auth_demo.py is the one doing the real work.
    """
    response = service_client.post(
        "/v1/agents/support:invoke",
        headers={**ACME, "origin": "https://evil.example"},
        json={"input": "hello"},
    )

    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


def test_only_a_trusted_front_door_may_assert_an_identity():
    """Forwarded identity headers are believed only when the forwarded-by marker is trusted.

    Behind a gateway the app stops re-validating tokens and reads the identity the
    gateway already verified from plain headers. That is only safe if a direct client
    cannot send the same headers, so the provider requires a marker naming a source on
    its allow-list. Here the *identity headers are identical* in all three requests —
    only the marker changes.
    """
    client = TestClient(create_app(auth=_trusting_gateway_auth(), agents=_served_agents()))
    identity = {
        "x-agentship-user": "amy",
        "x-agentship-tenant": "acme",
        "x-agentship-scopes": "agent:*:invoke",
    }
    turn = {"input": "hello"}

    spoofed = client.post("/v1/agents/support:invoke", headers=identity, json=turn)
    assert spoofed.status_code == 401
    assert spoofed.json()["code"] == "no_credentials"

    forged = client.post(
        "/v1/agents/support:invoke", headers={**identity, "x-forwarded-by": "attacker"}, json=turn
    )
    assert forged.status_code == 401
    assert forged.json()["code"] == "untrusted_source"

    forwarded = client.post(
        "/v1/agents/support:invoke",
        headers={**identity, "x-forwarded-by": TRUSTED_GATEWAY},
        json=turn,
    )
    assert forwarded.status_code == 200


def test_there_is_no_unauthenticated_dev_ingress_to_lock_down(service_client):
    """No `/api/*` dev router is mounted — the only ingress is the authenticated `/v1` one.

    The phase spec anticipates a loopback-only debug router that would need a
    non-loopback caller refused. This rebuild never grew one, and the safest version of
    that route is the one that does not exist, so this asserts the absence directly: no
    mounted path outside `/v1` and the documented public paths, and nothing under `/api`
    answering at all.
    """
    app = service_client.app
    mounted = {route.path for route in app.routes if hasattr(route, "path")}
    public = {"/healthz", "/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}

    assert not [path for path in mounted if path.startswith("/api")]
    assert all(path.startswith("/v1") or path in public for path in mounted), mounted
    assert service_client.get("/api/debug/agents").status_code in (401, 404)


def test_serve_binds_loopback_unless_told_otherwise():
    """`agentship serve` defaults to 127.0.0.1, so a bare run is not network-exposed.

    This is the loopback guarantee that actually ships: the default is "reachable from
    this machine only", and exposing the service is a deliberate `--host 0.0.0.0`.
    """
    defaults = {option.name: option.default for option in serve.params}

    assert defaults["host"] == "127.0.0.1"


def _trusting_gateway_auth() -> ForwardedHeaderAuthProvider:
    """A forwarded-header provider that trusts exactly one named gateway."""
    return ForwardedHeaderAuthProvider(trust_forwarded_from=[TRUSTED_GATEWAY])


def _served_agents() -> AgentRegistry:
    """The demo's echo agent, for the apps this module builds itself."""
    return AgentRegistry([build_agent(str(SERVICE_AGENT))])
