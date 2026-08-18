"""Tenant isolation — the one thing only the app can do.

A gateway routes *requests*; it cannot filter *our rows*. So the irreducible in-app job
is to make every stored read and write carry the authenticated tenant, and to refuse any
access that crosses a tenant boundary. This module is that seam:

* :class:`TenantScope` binds the authenticated caller's ``tenant_id``/``user_id`` into a
  contextvar for the duration of a request (the service enters it right after auth);
* :func:`current_tenant` / :func:`current_tenant_id` read the bound tenant — raising if
  none is bound, so a store can never silently fall back to a cross-tenant global read;
* :func:`guard_tenant` raises :class:`~agentship.errors.TenantViolation` when a resource
  is owned by a different tenant — the check every store call site runs.

The tenant is taken from the :class:`~agentship.context.Caller` only. There is
deliberately no argument by which a client-supplied tenant hint could widen a caller's
scope. The contextvar makes this safe under concurrency: each request/task sees only its
own binding. Stores land in later phases (session P02, memory P08, tasks P11); they import
these helpers so isolation is enforced identically wherever data is touched.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass

from .context import Caller
from .errors import AgentShipError, TenantViolation


@dataclass(frozen=True)
class TenantContext:
    """The tenant/user bound for the current request — the scope every store reads."""

    tenant_id: str
    user_id: str


#: The tenant bound for the current request/task, unset outside a :class:`TenantScope`.
_request_tenant: ContextVar[TenantContext | None] = ContextVar("request_tenant", default=None)


class TenantScope:
    """Bind an authenticated caller's tenant for the duration of a ``with`` block.

    The service enters this right after authentication and before running the agent, so
    every store touched inside the block sees the caller's tenant. Scopes nest and restore
    cleanly, and the binding is per-task, so concurrent requests never see each other's
    tenant.
    """

    def __init__(self, caller: Caller) -> None:
        """Capture the tenant/user to bind from ``caller`` (never from client input)."""
        self._context = TenantContext(tenant_id=caller.tenant_id, user_id=caller.user_id)
        self._token: Token[TenantContext | None] | None = None

    def __enter__(self) -> TenantContext:
        """Bind the tenant and return the :class:`TenantContext` now in effect."""
        self._token = _request_tenant.set(self._context)
        return self._context

    def __exit__(self, *exc: object) -> None:
        """Restore the previously-bound tenant (or unbind), even on error."""
        if self._token is not None:
            _request_tenant.reset(self._token)
            self._token = None


def current_tenant() -> TenantContext:
    """Return the tenant bound for the current request, or raise if none is bound.

    A missing binding is a programming error (a store used outside a request scope), not
    a reason to read globally — so this raises rather than returning a default.
    """
    context = _request_tenant.get()
    if context is None:
        raise AgentShipError(
            "no tenant is bound — a tenant-scoped store was used outside a TenantScope; "
            "enter TenantScope(caller) at the request boundary before touching stored data"
        )
    return context


def current_tenant_id() -> str:
    """Return the bound tenant id (convenience over :func:`current_tenant`)."""
    return current_tenant().tenant_id


def guard_tenant(owner_tenant_id: str) -> None:
    """Raise :class:`~agentship.errors.TenantViolation` unless the bound tenant owns it.

    Every store call site passes the owning tenant of the row it is about to return or
    mutate; if it does not match the tenant bound for this request, the access is refused.
    Raises the same missing-binding error as :func:`current_tenant` when used outside a
    scope, so an unscoped store access fails loudly rather than leaking across tenants.
    """
    acting = current_tenant_id()
    if owner_tenant_id != acting:
        raise TenantViolation(acting_tenant=acting, owner_tenant=owner_tenant_id)


__all__ = [
    "TenantScope",
    "TenantContext",
    "current_tenant",
    "current_tenant_id",
    "guard_tenant",
]
