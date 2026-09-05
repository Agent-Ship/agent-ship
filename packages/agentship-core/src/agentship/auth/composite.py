"""Accept several credential shapes at once, in precedence order.

A deployment may want to accept more than one kind of credential — gateway-forwarded
identity in production alongside a static API key for an internal script.
:class:`CompositeAuthProvider` holds an ordered list of providers and tries each in turn.

The dispatch is by convention, needing no extra interface on the providers: a provider
that does not recognize *its* credential raises ``AuthError("no_credentials")``, which
the composite treats as "not mine — try the next". Any other ``AuthError`` means the
provider recognized its credential and rejected it (a bad key, an expired token); that
propagates immediately, so a genuinely invalid credential is never masked by falling
through to a later provider.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..context import Caller
from ..errors import AuthError
from . import AuthProvider, RequestLike


class CompositeAuthProvider(AuthProvider):
    """Try each wrapped :class:`AuthProvider` in order until one authenticates.

    Precedence is list order: the first provider to recognize a credential wins. If no
    provider recognizes one, the composite raises ``no_credentials``.
    """

    def __init__(self, providers: Sequence[AuthProvider]) -> None:
        """Bind the providers to try, in precedence (first-to-last) order."""
        self._providers = tuple(providers)

    async def authenticate(self, request: RequestLike) -> Caller:
        """Return the first provider's caller, or raise the first non-fallthrough error."""
        for provider in self._providers:
            try:
                return await provider.authenticate(request)
            except AuthError as exc:
                if exc.code == "no_credentials":
                    continue  # this provider had nothing to authenticate — try the next
                raise  # recognized-but-invalid: do not fall through and mask it
        raise AuthError("no_credentials", "no configured auth provider recognized a credential")
