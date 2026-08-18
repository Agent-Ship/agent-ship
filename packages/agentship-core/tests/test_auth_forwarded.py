"""Phase 04 · C2 — the production auth path: :class:`ForwardedHeaderAuthProvider`.

Behind agentgateway, the gateway has already authenticated the caller and forwards
the verified identity as trusted headers. This provider reads those headers back into
a :class:`~agentship.context.Caller`. The gateway does **not** stop a direct client from
spoofing the same headers, so the two guards tested here are load-bearing and
non-optional: a required ``trust_forwarded_from`` allow-list (missing → fail fast at
construction), and rejecting any request whose forwarded-by marker is not on that list.
"""

from __future__ import annotations

import pytest
from agentship.auth import AuthProvider, ForwardedHeaderAuthProvider
from agentship.context import Caller
from agentship.errors import AuthError


def _request(headers: dict[str, str]):
    """A minimal RequestLike: an object exposing a ``headers`` mapping."""

    class _Req:
        pass

    req = _Req()
    req.headers = headers
    return req


def _provider(**overrides) -> ForwardedHeaderAuthProvider:
    """A provider trusting the ``agentgateway`` source, with defaults for the rest."""
    kwargs = {"trust_forwarded_from": ["agentgateway"]}
    kwargs.update(overrides)
    return ForwardedHeaderAuthProvider(**kwargs)


def test_is_an_auth_provider() -> None:
    """The forwarded-header provider is a concrete :class:`AuthProvider`."""
    assert isinstance(_provider(), AuthProvider)


def test_requires_a_trust_allow_list() -> None:
    """Construction fails fast when no ``trust_forwarded_from`` allow-list is given.

    Without the allow-list the provider would trust identity headers from anyone, so an
    empty (or missing) list is a configuration error refused at construction — never a
    silent "trust everybody" default.
    """
    with pytest.raises(ValueError, match="trust_forwarded_from"):
        ForwardedHeaderAuthProvider(trust_forwarded_from=[])


async def test_forwarded_identity_becomes_a_caller() -> None:
    """A request from a trusted source maps its identity headers to a Caller."""
    caller = await _provider().authenticate(
        _request(
            {
                "x-forwarded-by": "agentgateway",
                "x-agentship-tenant": "acme",
                "x-agentship-user": "u-1",
                "x-agentship-scopes": "agent:support:invoke agent:support:stream",
            }
        )
    )
    assert caller == Caller(
        tenant_id="acme",
        user_id="u-1",
        scopes={"agent:support:invoke", "agent:support:stream"},
        auth_method="forwarded",
    )


async def test_scopes_split_on_commas_or_whitespace() -> None:
    """The scopes header accepts either comma- or space-separated scope strings."""
    caller = await _provider().authenticate(
        _request(
            {
                "x-forwarded-by": "agentgateway",
                "x-agentship-user": "u",
                "x-agentship-scopes": "a:b:c, d:e:f\tg:h:i",
            }
        )
    )
    assert caller.scopes == frozenset({"a:b:c", "d:e:f", "g:h:i"})


async def test_tenant_defaults_when_header_absent() -> None:
    """A trusted request with no tenant header falls back to the single-tenant default."""
    caller = await _provider().authenticate(
        _request({"x-forwarded-by": "agentgateway", "x-agentship-user": "u"})
    )
    assert caller.tenant_id == "default"
    assert caller.scopes == frozenset()


async def test_untrusted_source_is_rejected() -> None:
    """A forwarded-by value not on the allow-list is rejected — the anti-spoof guard.

    A direct client can copy the identity headers, but it cannot come *from* the
    trusted gateway; a mismatched (or forged) forwarded-by marker is refused so spoofed
    identity never becomes a Caller.
    """
    with pytest.raises(AuthError) as excinfo:
        await _provider().authenticate(
            _request(
                {
                    "x-forwarded-by": "evil-proxy",
                    "x-agentship-user": "attacker",
                    "x-agentship-tenant": "victim",
                }
            )
        )
    assert excinfo.value.code == "untrusted_source"


async def test_missing_forwarded_by_marker_is_no_credentials() -> None:
    """A request with no forwarded-by marker is 'no credentials', not an attack.

    Reported as ``no_credentials`` (rather than ``untrusted_source``) so that inside a
    :class:`CompositeAuthProvider` a non-gateway request falls through to the next
    provider (e.g. the API-key one) instead of being rejected outright.
    """
    with pytest.raises(AuthError) as excinfo:
        await _provider().authenticate(_request({"x-agentship-user": "u"}))
    assert excinfo.value.code == "no_credentials"


async def test_trusted_source_but_no_user_is_no_credentials() -> None:
    """A trusted request that carries no user identity is a credential error, not a caller.

    Reaching the app through the gateway but without a forwarded user means the gateway
    was misconfigured; we refuse rather than invent an anonymous identity.
    """
    with pytest.raises(AuthError) as excinfo:
        await _provider().authenticate(_request({"x-forwarded-by": "agentgateway"}))
    assert excinfo.value.code == "no_credentials"


async def test_headers_are_configurable() -> None:
    """Header names can be overridden to match a gateway's Transformations policy."""
    provider = _provider(
        forwarded_by_header="x-envoy-internal",
        tenant_header="x-tenant",
        user_header="x-user",
        scopes_header="x-scopes",
    )
    caller = await provider.authenticate(
        _request({"x-envoy-internal": "agentgateway", "x-tenant": "t", "x-user": "u"})
    )
    assert (caller.tenant_id, caller.user_id) == ("t", "u")
