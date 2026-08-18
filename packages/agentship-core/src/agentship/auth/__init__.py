"""The authentication contract: turn a request's credentials into a :class:`Caller`.

An :class:`AuthProvider` is the one seam the service uses to answer "who is calling?".
It reads credentials off an incoming request (:class:`RequestLike`) and returns a
:class:`~agentship.context.Caller` — the canonical identity carrying ``tenant_id``,
``user_id``, the granted ``scopes``, and the ``auth_method`` — or raises
:class:`~agentship.errors.AuthError` when the credentials are missing or invalid.

Once a caller is known, :func:`authorize` checks whether its granted ``scopes`` allow
a given action on a given agent, raising :class:`~agentship.errors.AuthError` on denial.

This package is deliberately dependency-light: the contract imports only ``Caller`` and
``AuthError``, never an engine or a web framework, so the service can authenticate and
authorize a request without pulling the agent runtime in. The concrete adapters live in
sibling modules and are re-exported here:

- :class:`~agentship.auth.forwarded.ForwardedHeaderAuthProvider` — the **production**
  path, trusting identity headers a gateway already verified (guarded by an allow-list).
- :class:`~agentship.auth.api_key.ApiKeyAuthProvider` — the gateway-free dev/CI path.
- :class:`~agentship.auth.jwt.JwtAuthProvider` — an **optional** gateway-free OIDC hedge.
- :class:`~agentship.auth.composite.CompositeAuthProvider` — dispatches across the above.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from ..context import Caller
from ..errors import AuthError


@runtime_checkable
class RequestLike(Protocol):
    """The minimal request shape an :class:`AuthProvider` reads: its headers.

    Only ``headers`` (a case-insensitive string mapping) is required, so a provider
    stays framework-agnostic and unit-testable with a tiny fake object. Starlette's
    ``Request`` satisfies this structurally — its ``.headers`` is exactly such a
    mapping — so the FastAPI service passes the raw request through unchanged.
    """

    @property
    def headers(self) -> Mapping[str, str]:
        """Case-insensitive request headers (e.g. ``Authorization``, ``X-API-Key``)."""
        ...


class AuthProvider(ABC):
    """Base class for an authentication policy: ``request -> Caller``.

    A provider inspects a request's credentials and returns the authenticated
    :class:`~agentship.context.Caller`, or raises
    :class:`~agentship.errors.AuthError` when they are absent, malformed, or invalid.
    It must **never** return an anonymous caller on failure — a missing credential is
    an error, not a silent downgrade — so tenant scoping downstream can trust the
    identity. :meth:`authenticate` is ``async`` because real providers may do I/O
    (e.g. a JWT provider fetching JWKS), even though the API-key provider is trivial.
    """

    @abstractmethod
    async def authenticate(self, request: RequestLike) -> Caller:
        """Return the authenticated :class:`~agentship.context.Caller` for ``request``.

        Raises :class:`~agentship.errors.AuthError` (with a machine ``code``) when the
        request carries no credentials, or credentials that are malformed or invalid.
        """


def _scope_grants(granted: str, required: str) -> bool:
    """Whether one granted scope pattern covers a required ``agent:{name}:{verb}`` scope.

    A bare ``"*"`` grants everything. Otherwise both are split on ``":"`` and matched
    segment-by-segment: a granted segment matches when it is ``"*"`` (wildcard) or
    equals the required segment. Segment counts must match, so ``agent:support`` does
    not accidentally grant ``agent:support:invoke``. Examples: ``agent:*:invoke`` grants
    invoke on any agent; ``agent:support:*`` grants any verb on the support agent.
    """
    if granted == "*":
        return True
    granted_parts = granted.split(":")
    required_parts = required.split(":")
    if len(granted_parts) != len(required_parts):
        return False
    return all(g in ("*", r) for g, r in zip(granted_parts, required_parts, strict=True))


def authorize(caller: Caller, *, agent: str, verb: str) -> None:
    """Authorize ``caller`` to perform ``verb`` on ``agent``, or raise ``AuthError``.

    Builds the required scope string ``agent:{agent}:{verb}`` (e.g.
    ``agent:support:invoke``) and returns silently if any scope the caller holds grants
    it under the wildcard grammar in :func:`_scope_grants`. Otherwise raises
    :class:`~agentship.errors.AuthError` with code ``"forbidden"`` — which the service
    maps to HTTP 403 (distinct from an authentication failure's 401). The grammar's
    ``*`` / ``agent:*:{verb}`` / ``agent:{name}:*`` forms are the seam P13's RBAC role
    model maps onto, so roles resolve to these opaque scope strings without changing
    this check.
    """
    required = f"agent:{agent}:{verb}"
    if any(_scope_grants(scope, required) for scope in caller.scopes):
        return
    raise AuthError("forbidden", f"caller lacks the required scope {required!r}")


# Concrete adapters live in sibling modules; re-exported so callers keep importing them
# from ``agentship.auth`` regardless of which file they live in. Imported at the bottom
# to avoid a cycle (each adapter imports the contract defined above). Adapters are added
# here as their Phase-04 tasks land.
from .api_key import ApiKeyAuthProvider, ApiKeyStore, EnvApiKeyStore  # noqa: E402
from .forwarded import ForwardedHeaderAuthProvider  # noqa: E402

__all__ = [
    "AuthProvider",
    "RequestLike",
    "Caller",
    "authorize",
    "ForwardedHeaderAuthProvider",
    "ApiKeyAuthProvider",
    "ApiKeyStore",
    "EnvApiKeyStore",
]
