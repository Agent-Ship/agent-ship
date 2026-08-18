"""Phase 04 · C4 — tenant isolation, the headline of the runtime service.

A gateway can route requests but it cannot filter *our rows*. So the one thing only the
app can do is make every stored read/write carry the authenticated tenant. :class:`TenantScope`
binds the caller's tenant for the duration of a request; :func:`current_tenant` /
:func:`guard_tenant` are what a session/memory/task store consults so it can never read or
write across a tenant boundary. The tenant is sourced *only* from the authenticated
:class:`~agentship.context.Caller`; a client-supplied tenant hint is never consulted.
"""

from __future__ import annotations

import pytest
from agentship.context import Caller
from agentship.errors import AgentShipError, TenantViolation
from agentship.tenancy import (
    TenantScope,
    current_tenant,
    current_tenant_id,
    guard_tenant,
)


def test_scope_binds_tenant_and_user_from_caller() -> None:
    """Inside the scope, the bound tenant/user mirror the authenticated caller."""
    caller = Caller(tenant_id="acme", user_id="u-1", scopes={"*"})
    with TenantScope(caller):
        ctx = current_tenant()
        assert ctx.tenant_id == "acme"
        assert ctx.user_id == "u-1"
        assert current_tenant_id() == "acme"


def test_scope_unbinds_on_exit() -> None:
    """Leaving the scope clears the binding, so nothing leaks between requests."""
    with TenantScope(Caller(tenant_id="acme", user_id="u")):
        assert current_tenant().tenant_id == "acme"
    with pytest.raises(AgentShipError):
        current_tenant()


def test_scopes_nest_and_restore() -> None:
    """A nested scope restores the outer tenant on exit (concurrent-request safe)."""
    with TenantScope(Caller(tenant_id="outer", user_id="u")):
        with TenantScope(Caller(tenant_id="inner", user_id="u")):
            assert current_tenant_id() == "inner"
        assert current_tenant_id() == "outer"


def test_current_tenant_outside_a_scope_raises() -> None:
    """Reading the tenant with no scope bound is a hard error, not a silent default.

    A store must never fall back to a global read: if no tenant is bound, that is a bug
    to surface, not a cross-tenant query to allow.
    """
    with pytest.raises(AgentShipError):
        current_tenant()


def test_guard_allows_same_tenant() -> None:
    """A resource owned by the bound tenant passes the guard silently."""
    with TenantScope(Caller(tenant_id="acme", user_id="u")):
        guard_tenant("acme")  # returns None, no raise


def test_guard_rejects_other_tenant() -> None:
    """A resource owned by another tenant raises :class:`TenantViolation`."""
    with TenantScope(Caller(tenant_id="acme", user_id="u")):
        with pytest.raises(TenantViolation):
            guard_tenant("globex")


def test_guard_outside_a_scope_raises() -> None:
    """Guarding with no tenant bound is an error (a store must be inside a scope)."""
    with pytest.raises(AgentShipError):
        guard_tenant("acme")


def test_tenant_violation_is_in_the_taxonomy() -> None:
    """:class:`TenantViolation` is an :class:`AgentShipError` and names the tenants."""
    err = TenantViolation("acme", "globex")
    assert isinstance(err, AgentShipError)
    assert err.owner_tenant == "globex"
    assert err.acting_tenant == "acme"


def test_client_supplied_tenant_is_ignored() -> None:
    """The scope reads the tenant from the caller only — never a client-provided value.

    Even if a request carries its own tenant hint, ``TenantScope`` binds the caller's
    tenant, so there is no code path by which a client can widen its own scope.
    """
    caller = Caller(tenant_id="acme", user_id="u")
    with TenantScope(caller):
        # There is deliberately no argument by which a caller could override this.
        assert current_tenant_id() == "acme"
