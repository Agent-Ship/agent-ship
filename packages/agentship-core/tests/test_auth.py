"""Phase 04 · task 1 — the canonical identity + auth contract.

These pin the foundation every later auth task imports: the extended
:class:`~agentship.context.Caller` (now carrying ``scopes`` + ``auth_method``),
the :class:`~agentship.auth.AuthProvider` ABC, its :class:`~agentship.auth.RequestLike`
input shape, and the :class:`~agentship.errors.AuthError` a failed authentication raises.
"""

from __future__ import annotations

from abc import ABC

import pytest
from agentship.auth import AuthProvider, RequestLike, authorize
from agentship.context import Caller
from agentship.errors import AgentShipError, AuthError


def test_caller_defaults_are_single_tenant_and_unauthenticated() -> None:
    """A bare caller needs only ``user_id``: no scopes, no auth method, default tenant."""
    caller = Caller(user_id="u-1")
    assert caller.tenant_id == "default"
    assert caller.user_id == "u-1"
    assert caller.scopes == frozenset()
    assert caller.auth_method is None


def test_caller_carries_scopes_and_auth_method() -> None:
    """An authenticated caller records the scopes granted and how it was authenticated."""
    caller = Caller(
        tenant_id="acme",
        user_id="u-9",
        scopes={"agent:support:invoke", "agent:support:stream"},
        auth_method="jwt",
    )
    assert caller.tenant_id == "acme"
    assert caller.scopes == frozenset({"agent:support:invoke", "agent:support:stream"})
    assert caller.auth_method == "jwt"


def test_scopes_are_immutable() -> None:
    """``scopes`` is a frozenset, so a caller's grant cannot be mutated after auth."""
    caller = Caller(user_id="u", scopes={"a"})
    assert isinstance(caller.scopes, frozenset)


def test_auth_provider_is_an_abstract_base() -> None:
    """``AuthProvider`` is an ABC: it cannot be instantiated without an ``authenticate``."""
    assert issubclass(AuthProvider, ABC)
    with pytest.raises(TypeError):
        AuthProvider()  # type: ignore[abstract]


async def test_auth_provider_authenticate_returns_a_caller() -> None:
    """A concrete provider turns a request into a Caller; the contract is async."""

    class StaticAuth(AuthProvider):
        async def authenticate(self, request: RequestLike) -> Caller:
            token = request.headers.get("authorization", "")
            if token != "Bearer good":
                raise AuthError("invalid_token")
            return Caller(tenant_id="acme", user_id="u-1", scopes={"*"}, auth_method="jwt")

    class FakeRequest:
        headers = {"authorization": "Bearer good"}

    caller = await StaticAuth().authenticate(FakeRequest())
    assert caller.user_id == "u-1"
    assert caller.auth_method == "jwt"


async def test_auth_provider_rejects_bad_credentials_with_auth_error() -> None:
    """Bad or missing credentials raise AuthError, never return an anonymous caller."""

    class StaticAuth(AuthProvider):
        async def authenticate(self, request: RequestLike) -> Caller:
            if "authorization" not in request.headers:
                raise AuthError("no_credentials")
            raise AuthError("invalid_token")

    class NoAuthHeader:
        headers: dict[str, str] = {}

    with pytest.raises(AuthError) as excinfo:
        await StaticAuth().authenticate(NoAuthHeader())
    assert excinfo.value.code == "no_credentials"


def test_request_like_is_satisfied_by_anything_with_headers() -> None:
    """RequestLike is a structural (runtime-checkable) protocol keyed on ``headers``."""

    class Req:
        headers = {"authorization": "Bearer x"}

    assert isinstance(Req(), RequestLike)
    assert not isinstance(object(), RequestLike)


def test_auth_error_carries_a_machine_code_and_is_actionable() -> None:
    """AuthError exposes a stable ``code`` (for the 401 body) and falls under the taxonomy."""
    err = AuthError("invalid_api_key")
    assert err.code == "invalid_api_key"
    assert "invalid_api_key" in str(err)
    assert isinstance(err, AgentShipError)

    with_msg = AuthError("expired_token", "the JWT 'exp' is in the past")
    assert with_msg.code == "expired_token"
    assert "past" in str(with_msg)


def _caller_with(*scopes: str) -> Caller:
    """A caller holding exactly ``scopes`` (helper for the authorize matrix)."""
    return Caller(user_id="u", scopes=set(scopes))


@pytest.mark.parametrize(
    ("granted", "agent", "verb", "allowed"),
    [
        # Superuser wildcard grants everything.
        ("*", "support", "invoke", True),
        ("*", "billing", "stream", True),
        # Exact scope grants only that agent+verb.
        ("agent:support:invoke", "support", "invoke", True),
        ("agent:support:invoke", "support", "stream", False),
        ("agent:support:invoke", "billing", "invoke", False),
        # Verb wildcard: any verb on the named agent.
        ("agent:support:*", "support", "invoke", True),
        ("agent:support:*", "support", "stream", True),
        ("agent:support:*", "billing", "invoke", False),
        # Agent wildcard: the named verb on any agent.
        ("agent:*:invoke", "support", "invoke", True),
        ("agent:*:invoke", "billing", "invoke", True),
        ("agent:*:invoke", "support", "stream", False),
        # A shorter pattern must not grant a longer required scope.
        ("agent:support", "support", "invoke", False),
    ],
)
def test_authorize_grant_deny_matrix(granted: str, agent: str, verb: str, allowed: bool) -> None:
    """The wildcard grammar grants/denies exactly per the documented segment rules."""
    caller = _caller_with(granted)
    if allowed:
        authorize(caller, agent=agent, verb=verb)  # returns None, no raise
    else:
        with pytest.raises(AuthError) as excinfo:
            authorize(caller, agent=agent, verb=verb)
        assert excinfo.value.code == "forbidden"


def test_authorize_with_no_scopes_always_denies() -> None:
    """A caller with an empty scope set is denied every action (403 forbidden)."""
    with pytest.raises(AuthError) as excinfo:
        authorize(_caller_with(), agent="support", verb="invoke")
    assert excinfo.value.code == "forbidden"


def test_authorize_grants_when_any_held_scope_matches() -> None:
    """Authorization is a union: holding several scopes grants if any one matches."""
    caller = _caller_with("agent:billing:invoke", "agent:support:stream")
    authorize(caller, agent="support", verb="stream")
    with pytest.raises(AuthError):
        authorize(caller, agent="support", verb="invoke")
