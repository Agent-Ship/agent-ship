"""The durable-task skeleton: enqueue, inspect, list, and cancel — tenant-scoped.

This is the ``/v1/tasks`` surface (the full executor lands in P11). It exists now to fix
the tenant-isolation contract at a *store*: every task is stamped with the creating
caller's tenant, and every read/mutate runs ``guard_tenant`` so one tenant can neither see
nor cancel another's task — a cross-tenant read is hidden as 404, a cross-tenant write is a
403. The backing store is an in-memory dict on the app; it does not execute the task yet.
"""

from __future__ import annotations

import uuid

from agentship.auth import authorize
from agentship.context import Caller
from agentship.tenancy import current_tenant_id, guard_tenant
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from ..middleware import get_caller
from ..models.v1 import TaskCreateRequest, TaskRef

router = APIRouter(prefix="/v1/tasks", tags=["tasks"])


class TaskStore:
    """An in-memory task store that stamps each task with its owning tenant.

    Every entry records the tenant that created it; lookups return the owner so the route
    can run :func:`guard_tenant` before exposing or mutating a task. Replaced by the durable
    executor in P11 — the tenant-isolation contract it enforces does not change.
    """

    def __init__(self) -> None:
        """Start empty."""
        self._by_id: dict[str, tuple[str, TaskRef]] = {}

    def create(self, tenant_id: str, ref: TaskRef) -> None:
        """Store ``ref`` owned by ``tenant_id``."""
        self._by_id[ref.id] = (tenant_id, ref)

    def owner_and_ref(self, task_id: str) -> tuple[str, TaskRef]:
        """Return ``(owner_tenant_id, ref)`` for a task, or raise 404 if unknown."""
        entry = self._by_id.get(task_id)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"no task {task_id!r}")
        return entry

    def for_tenant(self, tenant_id: str) -> list[TaskRef]:
        """List the tasks owned by ``tenant_id`` (insertion order)."""
        return [ref for owner, ref in self._by_id.values() if owner == tenant_id]


def get_tasks(request: Request) -> TaskStore:
    """FastAPI dependency: the :class:`TaskStore` mounted on the app."""
    return request.app.state.tasks


def _guarded_ref(store: TaskStore, task_id: str) -> TaskRef:
    """Return a task only if the current tenant owns it, else raise a tenant violation.

    ``guard_tenant`` turns a cross-tenant read into 404 (a tenant cannot even learn the
    task exists) and a cross-tenant write into 403, per the error model.
    """
    owner, ref = store.owner_and_ref(task_id)
    guard_tenant(owner)
    return ref


@router.post("", response_model=TaskRef, status_code=status.HTTP_202_ACCEPTED)
async def create_task(
    body: TaskCreateRequest,
    response: Response,
    caller: Caller = Depends(get_caller),
    store: TaskStore = Depends(get_tasks),
) -> TaskRef:
    """Enqueue a task for the caller's tenant and return its handle (202 Accepted).

    Requires ``agent:{agent}:invoke`` on the target agent — enqueuing a run is an
    invocation. The task is owned by the caller's bound tenant; no client-supplied tenant
    can widen that. Execution is deferred to the P11 executor, so it starts as ``pending``.
    """
    authorize(caller, agent=body.agent, verb="invoke")
    ref = TaskRef(
        id=uuid.uuid4().hex,
        status="pending",
        agent=body.agent,
        session_id=body.session_id,
    )
    store.create(current_tenant_id(), ref)
    return ref


@router.get("", response_model=list[TaskRef])
async def list_tasks(
    _caller: Caller = Depends(get_caller),
    store: TaskStore = Depends(get_tasks),
) -> list[TaskRef]:
    """List the tasks owned by the caller's tenant (never another tenant's)."""
    return store.for_tenant(current_tenant_id())


@router.get("/{task_id}", response_model=TaskRef)
async def get_task(
    task_id: str,
    _caller: Caller = Depends(get_caller),
    store: TaskStore = Depends(get_tasks),
) -> TaskRef:
    """Return one task the caller's tenant owns (404 if unknown or owned by another tenant)."""
    return _guarded_ref(store, task_id)


@router.post("/{task_id}:cancel", response_model=TaskRef)
async def cancel_task(
    task_id: str,
    _caller: Caller = Depends(get_caller),
    store: TaskStore = Depends(get_tasks),
) -> TaskRef:
    """Cancel a task the caller's tenant owns (403 if it belongs to another tenant)."""
    ref = _guarded_ref(store, task_id)
    ref.status = "cancelled"
    return ref
