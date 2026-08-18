"""The production auth path: trust the identity a gateway already verified.

Behind agentgateway (or any front door that authenticates callers), the gateway
validates the credential and forwards the resulting identity to the app as plain
headers — its "Transformations" policy sets e.g. ``x-agentship-user`` from the verified
token. :class:`ForwardedHeaderAuthProvider` reads those headers back into a
:class:`~agentship.context.Caller`, so the app never re-validates a token in the hot path.

The gateway does not, by itself, stop a direct client from sending the same headers, so
two guards here are non-optional (see the phase spec, DESIGN §2.1):

* a required ``trust_forwarded_from`` allow-list — without it the provider would trust
  identity from anyone, so an empty list is refused at construction (fail fast); and
* a per-request check that the forwarded-by marker names a source on that allow-list —
  a forged or missing marker is rejected before any identity header is read.

Network-level sole-ingress (the app only accepts connections from the gateway) is the
primary defense; this marker check is defense-in-depth for that seam.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from ..context import Caller
from ..errors import AuthError
from . import AuthProvider, RequestLike

#: Split a scopes header on any run of commas/whitespace, so a gateway may forward
#: scopes as ``"a b c"`` or ``"a,b,c"`` (or a mix) without the app caring which.
_SCOPE_SEPARATORS = re.compile(r"[,\s]+")


class ForwardedHeaderAuthProvider(AuthProvider):
    """Turn a gateway's forwarded identity headers into a :class:`Caller`.

    The default header names (``x-forwarded-by``, ``x-agentship-tenant``,
    ``x-agentship-user``, ``x-agentship-scopes``) can each be overridden to match the
    names a particular gateway's forwarding policy emits.
    """

    def __init__(
        self,
        *,
        trust_forwarded_from: Iterable[str],
        forwarded_by_header: str = "x-forwarded-by",
        tenant_header: str = "x-agentship-tenant",
        user_header: str = "x-agentship-user",
        scopes_header: str = "x-agentship-scopes",
    ) -> None:
        """Configure the trusted sources and (optionally) the identity header names.

        ``trust_forwarded_from`` is the set of forwarded-by values that identify the
        gateway(s) allowed to forward identity to this app. It must be non-empty: an
        empty allow-list would mean "trust anyone", so it is refused here rather than
        becoming a silent security hole.
        """
        trusted = frozenset(trust_forwarded_from)
        if not trusted:
            raise ValueError(
                "ForwardedHeaderAuthProvider requires a non-empty trust_forwarded_from "
                "allow-list naming the gateway(s) whose forwarded identity to trust — "
                "an empty list would trust spoofed identity from any client"
            )
        self._trusted = trusted
        self._forwarded_by_header = forwarded_by_header
        self._tenant_header = tenant_header
        self._user_header = user_header
        self._scopes_header = scopes_header

    async def authenticate(self, request: RequestLike) -> Caller:
        """Return the :class:`Caller` the gateway forwarded, or raise :class:`AuthError`.

        Rejects the request (``untrusted_source``) unless its forwarded-by marker is on
        the allow-list, then reads tenant/user/scopes from the identity headers. A
        trusted request that carries no user is a ``no_credentials`` error rather than an
        invented anonymous identity — that means the gateway was misconfigured.
        """
        forwarded_by = request.headers.get(self._forwarded_by_header)
        if forwarded_by is None:
            # No forwarded-identity marker at all: this request was not shaped by a
            # gateway, so this provider has nothing to authenticate. Reported as
            # ``no_credentials`` (not ``untrusted_source``) so a CompositeAuthProvider
            # falls through to the next provider rather than treating it as an attack.
            raise AuthError(
                "no_credentials",
                f"no {self._forwarded_by_header!r} marker — request did not come via a gateway",
            )
        if forwarded_by not in self._trusted:
            raise AuthError(
                "untrusted_source",
                "request did not arrive from a trusted gateway "
                f"(forwarded-by {forwarded_by!r} is not on the allow-list)",
            )
        user_id = request.headers.get(self._user_header)
        if not user_id:
            raise AuthError(
                "no_credentials",
                "trusted gateway forwarded no user identity — check its identity-forwarding "
                f"policy sets the {self._user_header!r} header",
            )
        tenant_id = request.headers.get(self._tenant_header) or "default"
        scopes = _parse_scopes(request.headers.get(self._scopes_header, ""))
        return Caller(
            tenant_id=tenant_id, user_id=user_id, scopes=scopes, auth_method="forwarded"
        )


def _parse_scopes(raw: str) -> frozenset[str]:
    """Parse a forwarded scopes header into a set, tolerant of comma/space separators."""
    return frozenset(part for part in _SCOPE_SEPARATORS.split(raw.strip()) if part)
