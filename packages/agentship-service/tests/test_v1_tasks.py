"""The v1 task skeleton — and the tenant-isolation proof at a store.

The headline of P04 is that a gateway can route requests but cannot filter our rows, so the
app must. These tests create tasks as one tenant and show a *different* tenant can neither
read (hidden as 404) nor cancel (403) them, while the owner can — the isolation contract
every future store inherits.
"""

from __future__ import annotations

import json

from agentship.auth import ApiKeyAuthProvider, EnvApiKeyStore
from agentship.runtime import build_agent
from agentship.spec import AgentSpec
from agentship_service import AgentRegistry, create_app
from fastapi.testclient import TestClient

#: Two callers in two tenants, each able to invoke ``support``.
_KEYS = json.dumps(
    [
        {"key": "acme", "user": "u1", "tenant": "acme", "scopes": ["*"]},
        {"key": "beta", "user": "u2", "tenant": "beta", "scopes": ["*"]},
    ]
)


def _client() -> TestClient:
    """Build a client over an app serving one ``support`` agent."""
    agents = AgentRegistry([build_agent(AgentSpec(name="support", engine="echo"))])
    auth = ApiKeyAuthProvider(EnvApiKeyStore(raw=_KEYS))
    return TestClient(create_app(auth=auth, agents=agents))


def _create(client: TestClient, key: str) -> str:
    """Enqueue a task as the caller holding ``key`` and return its id."""
    resp = client.post(
        "/v1/tasks",
        headers={"x-api-key": key},
        json={"agent": "support", "input": "do it"},
    )
    assert resp.status_code == 202
    return resp.json()["id"]


def test_create_returns_202_pending_task() -> None:
    """Enqueuing a task returns 202 with a pending handle for the caller's tenant."""
    client = _client()
    resp = client.post(
        "/v1/tasks",
        headers={"x-api-key": "acme"},
        json={"agent": "support", "input": "hi"},
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "pending"
    assert body["agent"] == "support"
    assert body["id"]


def test_create_requires_invoke_scope() -> None:
    """Enqueuing a task requires invoke scope on the target agent → 403 without it."""
    keys = json.dumps(
        [{"key": "ro", "user": "u", "tenant": "acme", "scopes": ["agent:other:invoke"]}]
    )
    agents = AgentRegistry([build_agent(AgentSpec(name="support", engine="echo"))])
    auth = ApiKeyAuthProvider(EnvApiKeyStore(raw=keys))
    client = TestClient(create_app(auth=auth, agents=agents))
    resp = client.post(
        "/v1/tasks", headers={"x-api-key": "ro"}, json={"agent": "support", "input": "hi"}
    )
    assert resp.status_code == 403


def test_owner_can_read_own_task() -> None:
    """The creating tenant can read back its own task."""
    client = _client()
    task_id = _create(client, "acme")
    resp = client.get(f"/v1/tasks/{task_id}", headers={"x-api-key": "acme"})
    assert resp.status_code == 200
    assert resp.json()["id"] == task_id


def test_other_tenant_cannot_read_task_hidden_as_404() -> None:
    """A different tenant reading another's task gets 404 — it cannot even learn it exists."""
    client = _client()
    task_id = _create(client, "acme")
    resp = client.get(f"/v1/tasks/{task_id}", headers={"x-api-key": "beta"})
    assert resp.status_code == 404
    assert resp.json()["code"] == "not_found"


def test_other_tenant_cannot_cancel_task_forbidden() -> None:
    """A different tenant cancelling another's task gets 403 (a write, not hidden)."""
    client = _client()
    task_id = _create(client, "acme")
    resp = client.post(f"/v1/tasks/{task_id}:cancel", headers={"x-api-key": "beta"})
    assert resp.status_code == 403
    assert resp.json()["code"] == "forbidden"


def test_owner_can_cancel_own_task() -> None:
    """The owning tenant can cancel its task, moving it to cancelled."""
    client = _client()
    task_id = _create(client, "acme")
    resp = client.post(f"/v1/tasks/{task_id}:cancel", headers={"x-api-key": "acme"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"


def test_list_is_scoped_to_caller_tenant() -> None:
    """Listing tasks shows only the caller's tenant's tasks, never another's."""
    client = _client()
    acme_id = _create(client, "acme")
    _create(client, "beta")

    acme_list = client.get("/v1/tasks", headers={"x-api-key": "acme"}).json()
    assert [t["id"] for t in acme_list] == [acme_id]

    beta_list = client.get("/v1/tasks", headers={"x-api-key": "beta"}).json()
    assert acme_id not in {t["id"] for t in beta_list}
    assert len(beta_list) == 1


def test_read_unknown_task_is_404() -> None:
    """Reading a task id that does not exist → 404."""
    client = _client()
    resp = client.get("/v1/tasks/does-not-exist", headers={"x-api-key": "acme"})
    assert resp.status_code == 404
