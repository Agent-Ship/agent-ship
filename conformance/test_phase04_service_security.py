"""Phase 04 conformance cells: auth, authorization, tenant isolation, stream contract (§11).

Named cells that pin P04's promises against the runtime service, engine-agnostically (over the
in-memory echo engine, no DB, no live SaaS — the CI-gate tier):

* ``auth_apikey_roundtrip`` — a valid API key authenticates; a missing/unknown key is 401.
* ``authz_scope_enforced`` — a caller without ``agent:{name}:invoke`` is 403; with it, 200.
* ``tenant_isolation_task`` — a task created by tenant A is invisible (404) and un-cancellable
  (403) to tenant B, while its owner reads and cancels it — the guarantee no gateway can give.
* ``stream_event_contract`` — every SSE frame's ``type`` is in the §7 union and its ``seq`` is
  gap-free and monotonic from 0.
* ``no_unauthenticated_ingress`` — the §13.8 lockdown, honestly scoped to this rebuild: there is
  **no** unauthenticated route to any agent/task; every ``/v1`` surface demands a credential
  (the legacy ``/api/*`` debug router and Studio do not exist here, so the only ingress is the
  authenticated ``/v1`` one).
"""

from __future__ import annotations

import json

from agentship.auth import ApiKeyAuthProvider, EnvApiKeyStore
from agentship.runtime import build_agent
from agentship.spec import AgentSpec
from agentship_service import AgentRegistry, create_app
from fastapi.testclient import TestClient

#: The §7 SSE/WS event contract: every emitted frame's ``type`` must be one of these.
_STREAM_EVENT_TYPES = frozenset(
    {"session", "token", "tool_call", "tool_result", "guard", "content", "done", "error"}
)

_KEYS = json.dumps(
    [
        {"key": "acme", "user": "u1", "tenant": "acme", "scopes": ["*"]},
        {"key": "beta", "user": "u2", "tenant": "beta", "scopes": ["*"]},
        {"key": "narrow", "user": "u3", "tenant": "acme", "scopes": ["agent:other:invoke"]},
    ]
)


def _client() -> TestClient:
    """A client over an app serving one streaming ``support`` agent under the shared key table."""
    agents = AgentRegistry([build_agent(AgentSpec(name="support", engine="echo", streaming=True))])
    auth = ApiKeyAuthProvider(EnvApiKeyStore(raw=_KEYS))
    return TestClient(create_app(auth=auth, agents=agents))


def test_auth_apikey_roundtrip() -> None:
    """Cell ``auth_apikey_roundtrip``: a valid key authenticates; missing/unknown → 401."""
    client = _client()
    ok = client.post(
        "/v1/agents/support:invoke", headers={"x-api-key": "acme"}, json={"input": "hi"}
    )
    assert ok.status_code == 200

    missing = client.post("/v1/agents/support:invoke", json={"input": "hi"})
    assert missing.status_code == 401

    unknown = client.post(
        "/v1/agents/support:invoke", headers={"x-api-key": "nope"}, json={"input": "hi"}
    )
    assert unknown.status_code == 401


def test_authz_scope_enforced() -> None:
    """Cell ``authz_scope_enforced``: without ``agent:support:invoke`` → 403; with it → 200."""
    client = _client()
    denied = client.post(
        "/v1/agents/support:invoke", headers={"x-api-key": "narrow"}, json={"input": "hi"}
    )
    assert denied.status_code == 403

    allowed = client.post(
        "/v1/agents/support:invoke", headers={"x-api-key": "acme"}, json={"input": "hi"}
    )
    assert allowed.status_code == 200


def test_tenant_isolation_task() -> None:
    """Cell ``tenant_isolation_task``: tenant B cannot read (404) or cancel (403) A's task."""
    client = _client()
    created = client.post(
        "/v1/tasks", headers={"x-api-key": "acme"}, json={"agent": "support", "input": "do it"}
    )
    assert created.status_code == 202
    task_id = created.json()["id"]

    assert client.get(f"/v1/tasks/{task_id}", headers={"x-api-key": "beta"}).status_code == 404
    assert (
        client.post(f"/v1/tasks/{task_id}:cancel", headers={"x-api-key": "beta"}).status_code == 403
    )
    # The owner still reads and cancels it.
    assert client.get(f"/v1/tasks/{task_id}", headers={"x-api-key": "acme"}).status_code == 200
    assert (
        client.post(f"/v1/tasks/{task_id}:cancel", headers={"x-api-key": "acme"}).status_code == 200
    )


def test_stream_event_contract() -> None:
    """Cell ``stream_event_contract``: every SSE frame ∈ the §7 union; ``seq`` gap-free from 0."""
    client = _client()
    seqs: list[int] = []
    with client.stream(
        "POST",
        "/v1/agents/support:stream",
        headers={"x-api-key": "acme"},
        json={"input": "hi"},
    ) as resp:
        assert resp.status_code == 200
        for line in resp.iter_lines():
            if line.startswith("data:"):
                frame = json.loads(line[len("data:") :].strip())
                assert frame["type"] in _STREAM_EVENT_TYPES
                seqs.append(frame["seq"])
    assert seqs == list(range(len(seqs)))
    assert seqs  # at least the session frame was emitted


def test_no_unauthenticated_ingress() -> None:
    """Cell ``no_unauthenticated_ingress`` (§13.8): no ``/v1`` route is reachable unauthenticated.

    The rebuild ships no legacy ``/api/*`` debug router and no Studio, so the only network
    surface is the authenticated ``/v1`` one; the liveness probe is the sole public path.
    """
    client = _client()
    protected = [
        ("POST", "/v1/agents/support:invoke"),
        ("POST", "/v1/agents/support:stream"),
        ("GET", "/v1/agents"),
        ("GET", "/v1/agents/support"),
        ("POST", "/v1/tasks"),
        ("GET", "/v1/tasks"),
    ]
    for method, path in protected:
        resp = client.request(method, path, json={"input": "x", "agent": "support"})
        assert resp.status_code == 401, f"{method} {path} was reachable unauthenticated"

    # The only public path is the liveness probe.
    assert client.get("/healthz").status_code == 200
