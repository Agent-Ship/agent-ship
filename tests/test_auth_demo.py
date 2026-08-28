"""Demo slice (Phase 07): one route, three callers, three outcomes.

Every test here posts the *same* request to the *same* endpoint. The only thing that
changes is the credential, so the difference in outcome is visibly the caller:

* no credential           -> 401 Unauthorized  (we do not know who you are)
* `acme-key`              -> 200 OK            (known, and scoped to invoke)
* `reader-key`            -> 403 Forbidden     (known, but never granted the scope)

That 401/403 split is the part worth proving. Authentication and authorization are two
different questions, and collapsing them — answering 401 to a scope failure, or 403 to a
missing credential — tells a caller the wrong thing to fix. The 403 case is also what
shows ``authorize()`` is really running: `reader-key` is a perfectly valid key.

Keyless: the API-key provider is the gateway-free dev/CI path and needs no external IdP.

    pytest tests/test_auth_demo.py
"""

from __future__ import annotations

import pytest
from conftest import ACME, READER
from starlette.websockets import WebSocketDisconnect

#: The one route every caller in this file hits.
INVOKE = "/v1/agents/support:invoke"

#: The one request body every caller in this file sends.
TURN = {"input": "hello"}


def test_no_credential_is_401(service_client):
    """A request with no API key is rejected as unauthenticated, with an actionable detail.

    The provider never falls back to an anonymous caller — a missing credential is an
    error, not a silent downgrade to some default identity.
    """
    response = service_client.post(INVOKE, json=TURN)

    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/problem+json")
    problem = response.json()
    assert problem["code"] == "no_credentials"
    assert "x-api-key" in problem["detail"]


def test_an_unrecognised_key_is_also_401_but_says_so(service_client):
    """A key that is not in the table is 401 with ``invalid_api_key`` — a distinct code.

    Same status as no credential (both mean "we do not know who you are"), but the
    machine ``code`` differs so a client can tell "you sent nothing" from "you sent
    something wrong".
    """
    response = service_client.post(INVOKE, headers={"x-api-key": "not-a-real-key"}, json=TURN)

    assert response.status_code == 401
    assert response.json()["code"] == "invalid_api_key"


def test_a_valid_scoped_key_is_200(service_client):
    """`acme-key` holds ``agent:*:invoke``, so the same request now runs the turn."""
    response = service_client.post(INVOKE, headers=ACME, json=TURN)

    assert response.status_code == 200
    assert response.json()["output"] == "echo: hello"


def test_a_valid_key_without_the_scope_is_403(service_client):
    """`reader-key` authenticates fine but lacks ``agent:support:invoke`` — 403, not 401.

    This is the cell that proves authorization is a real second check: the credential is
    valid and the caller is known, and the request is still refused. The detail names the
    exact scope that was missing, so the fix is obvious.
    """
    response = service_client.post(INVOKE, headers=READER, json=TURN)

    assert response.status_code == 403
    problem = response.json()
    assert problem["code"] == "forbidden"
    assert "agent:support:invoke" in problem["detail"]


def test_the_bearer_header_carries_the_key_too(service_client):
    """The same key works as ``Authorization: Bearer <key>``, for clients that only speak OAuth."""
    response = service_client.post(
        INVOKE, headers={"authorization": "Bearer acme-key"}, json=TURN
    )

    assert response.status_code == 200


def test_the_websocket_handshake_refuses_an_unauthenticated_caller(service_client):
    """An unauthenticated `/live` handshake is closed, never accepted.

    A socket has no status codes, so the middleware refuses the handshake outright rather
    than accepting and then complaining — an unauthenticated client never gets a socket.
    """
    with pytest.raises(WebSocketDisconnect):
        with service_client.websocket_connect("/v1/agents/support/live") as socket:
            socket.receive_json()


def test_liveness_is_the_only_route_that_needs_no_credential(service_client):
    """Every `/v1` route demands a credential; `/healthz` is the single public path.

    This is the "no unauthenticated ingress" promise stated as a list: if a route is
    added without auth, this test goes red.
    """
    protected = [
        ("POST", "/v1/agents/support:invoke"),
        ("POST", "/v1/agents/support:stream"),
        ("GET", "/v1/agents"),
        ("GET", "/v1/agents/support"),
        ("POST", "/v1/tasks"),
        ("GET", "/v1/tasks"),
    ]
    for method, path in protected:
        response = service_client.request(method, path, json={"input": "x", "agent": "support"})
        assert response.status_code == 401, f"{method} {path} was reachable unauthenticated"

    assert service_client.get("/healthz").status_code == 200
