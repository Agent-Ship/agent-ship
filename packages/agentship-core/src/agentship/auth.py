"""The authentication contract: turn a request's credentials into a :class:`Caller`.

An :class:`AuthProvider` is the one seam the service uses to answer "who is calling?".
It reads credentials off an incoming request (:class:`RequestLike`) and returns a
:class:`~agentship.context.Caller` — the canonical identity carrying ``tenant_id``,
``user_id``, the granted ``scopes``, and the ``auth_method`` — or raises
:class:`~agentship.errors.AuthError` when the credentials are missing or invalid.

This module is deliberately dependency-light: it imports only ``Caller`` and
``AuthError``, never an engine, so the service can authenticate a request without
pulling the agent runtime in. Concrete adapters (API key, JWT, composite), their
registry, and authorization (``authorize`` over a caller's scopes) land in the
following Phase-04 tasks; this file fixes only the contract they implement.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from .context import Caller


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


__all__ = ["AuthProvider", "RequestLike", "Caller"]
