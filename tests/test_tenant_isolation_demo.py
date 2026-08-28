"""Demo slice (Phase 08): tenant B cannot see, read, or touch tenant A's rows.

This is the one guarantee a gateway structurally cannot give you. A routing proxy sees
requests; it has no idea which rows in our store belong to whom. So the filter has to
live in the app, on every read and every write, sourced only from the authenticated
caller.

Two callers here, `acme-key` and `beta-key`, differ only in their tenant. Tenant acme
creates a task carrying a session id and reads it back; tenant beta then asks for that
exact id.

**The cross-tenant read is 404, not 403.** That is deliberate and it is the whole point
of the slice. A 403 would confirm the row exists — a caller could enumerate ids and learn
what a competitor is running from the status codes alone. A 404 says nothing. A
cross-tenant *write* is 403, because by then the caller has already been told (by the 404
on read) that there is nothing there, and a 403 on cancel is the honest answer.

Keyless and offline: in-memory rows, the `echo` engine, no DB.

    pytest tests/test_tenant_isolation_demo.py
"""

from __future__ import annotations

import pytest
from conftest import ACME, BETA


@pytest.fixture
def acme_task(service_client):
    """A task created by tenant acme, carrying a session id — the row tenant beta will hunt for."""
    response = service_client.post(
        "/v1/tasks",
        headers=ACME,
        json={"agent": "support", "input": "look into this", "session_id": "acme-session-1"},
    )
    assert response.status_code == 202
    return response.json()


def test_the_owning_tenant_reads_its_own_task_back(service_client, acme_task):
    """Tenant acme reads the task it created, session id intact — the baseline the rest contrasts."""
    response = service_client.get(f"/v1/tasks/{acme_task['id']}", headers=ACME)

    assert response.status_code == 200
    assert response.json()["session_id"] == "acme-session-1"


def test_the_other_tenant_gets_404_not_403(service_client, acme_task):
    """Tenant beta asks for that exact task id and is told it does not exist.

    404, not 403: a 403 would leak that the id is real. Beta learns nothing about acme's
    work from the status code.
    """
    response = service_client.get(f"/v1/tasks/{acme_task['id']}", headers=BETA)

    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


def test_the_other_tenant_gets_the_same_404_for_an_id_that_never_existed(
    service_client, acme_task
):
    """A real-but-foreign id and a made-up id are indistinguishable to tenant beta.

    Existence-hiding only works if the two cases are identical, so this asserts the
    responses match — status and machine code alike. If a future change made the foreign
    id 403, this test would go red alongside the one above.
    """
    foreign = service_client.get(f"/v1/tasks/{acme_task['id']}", headers=BETA)
    imaginary = service_client.get("/v1/tasks/0000000000000000", headers=BETA)

    assert foreign.status_code == imaginary.status_code == 404
    assert foreign.json()["code"] == imaginary.json()["code"]


def test_the_other_tenant_cannot_cancel_it(service_client, acme_task):
    """A cross-tenant cancel is refused with 403, and the task really is left untouched.

    Reading back as the owner afterwards is the part that matters: a status code alone
    would not prove the write did not land.
    """
    denied = service_client.post(f"/v1/tasks/{acme_task['id']}:cancel", headers=BETA)
    assert denied.status_code == 403

    still_pending = service_client.get(f"/v1/tasks/{acme_task['id']}", headers=ACME)
    assert still_pending.json()["status"] == "pending"


def test_the_owner_can_cancel_it(service_client, acme_task):
    """The owning tenant's cancel succeeds — the guard denies the foreigner, not the feature."""
    response = service_client.post(f"/v1/tasks/{acme_task['id']}:cancel", headers=ACME)

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"


def test_listing_tasks_shows_only_your_own(service_client, acme_task):
    """`GET /v1/tasks` is filtered by the caller's tenant, so beta's list is empty.

    The list route is the easiest place to leak everything at once, so it gets its own
    cell rather than riding on the by-id checks.
    """
    acme_ids = [task["id"] for task in service_client.get("/v1/tasks", headers=ACME).json()]
    beta_ids = [task["id"] for task in service_client.get("/v1/tasks", headers=BETA).json()]

    assert acme_task["id"] in acme_ids
    assert beta_ids == []


def test_a_tenant_id_in_the_request_body_has_no_effect(service_client, acme_task):
    """A client-supplied `tenant_id` cannot widen what a caller sees — the wire refuses it.

    The tenant comes only from the authenticated caller, so the request models carry no
    tenant field at all and forbid extras. A body hint is therefore rejected outright
    (422) rather than quietly ignored, which is the stronger outcome: there is no field
    to smuggle a tenant through. The read-back confirms the obvious consequence — beta
    still sees nothing of acme's.
    """
    smuggled = service_client.post(
        "/v1/tasks",
        headers=BETA,
        json={"agent": "support", "input": "whose task is this", "tenant_id": "acme"},
    )
    assert smuggled.status_code == 422
    assert "body.tenant_id" in smuggled.json()["detail"]

    invoked = service_client.post(
        "/v1/agents/support:invoke",
        headers=BETA,
        json={"input": "hello", "tenant_id": "acme"},
    )
    assert invoked.status_code == 422

    assert service_client.get(f"/v1/tasks/{acme_task['id']}", headers=BETA).status_code == 404
