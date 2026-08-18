"""Phase 04 · C2 — :class:`CompositeAuthProvider`, trying providers in precedence order.

A deployment may accept more than one credential shape at once — e.g. gateway-forwarded
identity in production plus a static API key for a health-check script. The composite
tries its providers in order. A provider that does not see *its* credential raises
``no_credentials`` and the composite falls through to the next; but a provider that
recognizes its credential and finds it *invalid* stops the chain (its error propagates),
so a bad token is never masked by a later provider.
"""

from __future__ import annotations

import pytest
from agentship.auth import AuthProvider, CompositeAuthProvider, RequestLike
from agentship.context import Caller
from agentship.errors import AuthError


def _request(headers: dict[str, str]):
    class _Req:
        pass

    req = _Req()
    req.headers = headers
    return req


class _SeesHeader(AuthProvider):
    """A stub provider that authenticates iff ``header`` is present and correct."""

    def __init__(self, header: str, good_value: str, user: str) -> None:
        self._header = header
        self._good = good_value
        self._user = user

    async def authenticate(self, request: RequestLike) -> Caller:
        value = request.headers.get(self._header)
        if value is None:
            raise AuthError("no_credentials")  # not my credential → fall through
        if value != self._good:
            raise AuthError("invalid_token")  # my credential, but bad → stop the chain
        return Caller(user_id=self._user, scopes={"*"}, auth_method="stub")


def test_is_an_auth_provider() -> None:
    """The composite is itself an :class:`AuthProvider`."""
    assert isinstance(CompositeAuthProvider([]), AuthProvider)


async def test_first_matching_provider_wins() -> None:
    """The earliest provider that sees its credential authenticates the request."""
    composite = CompositeAuthProvider(
        [_SeesHeader("x-a", "ok", "user-a"), _SeesHeader("x-b", "ok", "user-b")]
    )
    caller = await composite.authenticate(_request({"x-b": "ok"}))
    assert caller.user_id == "user-b"


async def test_precedence_order_is_respected() -> None:
    """When two providers both match, the earlier one in the list takes precedence."""
    composite = CompositeAuthProvider(
        [_SeesHeader("x-a", "ok", "first"), _SeesHeader("x-a", "ok", "second")]
    )
    caller = await composite.authenticate(_request({"x-a": "ok"}))
    assert caller.user_id == "first"


async def test_recognized_but_invalid_stops_the_chain() -> None:
    """A recognized-but-invalid credential propagates; a later provider cannot mask it."""
    composite = CompositeAuthProvider(
        [_SeesHeader("x-a", "ok", "user-a"), _SeesHeader("x-b", "ok", "user-b")]
    )
    with pytest.raises(AuthError) as excinfo:
        # x-a is present but wrong → provider A raises invalid_token; even though x-b
        # is valid, the composite must not fall through to B.
        await composite.authenticate(_request({"x-a": "wrong", "x-b": "ok"}))
    assert excinfo.value.code == "invalid_token"


async def test_no_credentials_when_nothing_matches() -> None:
    """If no provider sees a credential, the composite raises ``no_credentials``."""
    composite = CompositeAuthProvider([_SeesHeader("x-a", "ok", "u")])
    with pytest.raises(AuthError) as excinfo:
        await composite.authenticate(_request({}))
    assert excinfo.value.code == "no_credentials"


async def test_empty_composite_is_no_credentials() -> None:
    """A composite with no providers authenticates nobody."""
    with pytest.raises(AuthError) as excinfo:
        await CompositeAuthProvider([]).authenticate(_request({"x-a": "ok"}))
    assert excinfo.value.code == "no_credentials"
