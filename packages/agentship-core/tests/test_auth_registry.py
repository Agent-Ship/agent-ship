"""Phase 04 · C2 — auth providers are discoverable and built from config by name.

A deployment selects its auth provider by name (in service config); a vendor can ship a
custom one under the ``agentship.auth_providers`` entry-point group with no core edit.
:func:`build_auth_provider` resolves a name to a configured provider and fails fast — with
the list of installed names — when the name is unknown, which is what ``agentship serve``
and ``doctor`` use to reject a misconfigured or uninstalled provider before binding a port.
"""

from __future__ import annotations

import json

import pytest
from agentship.auth import (
    ApiKeyAuthProvider,
    CompositeAuthProvider,
    ForwardedHeaderAuthProvider,
)
from agentship.auth.registry import AUTH_PROVIDERS, build_auth_provider
from agentship.errors import AgentShipError, AuthError


def _request(headers: dict[str, str]):
    class _Req:
        pass

    req = _Req()
    req.headers = headers
    return req


def test_builtin_providers_are_discoverable() -> None:
    """The four built-in providers register under the entry-point group."""
    names = AUTH_PROVIDERS.names()
    assert {"forwarded", "api_key", "jwt", "composite"} <= set(names)


def test_build_forwarded_from_config() -> None:
    """The ``forwarded`` factory builds a configured provider from its allow-list."""
    provider = build_auth_provider("forwarded", {"trust_forwarded_from": ["agentgateway"]})
    assert isinstance(provider, ForwardedHeaderAuthProvider)


def test_forwarded_config_missing_allow_list_fails_fast() -> None:
    """Omitting the required allow-list surfaces as a construction error."""
    with pytest.raises(ValueError, match="trust_forwarded_from"):
        build_auth_provider("forwarded", {})


async def test_build_api_key_from_config() -> None:
    """The ``api_key`` factory wires an EnvApiKeyStore from the named env var."""
    keys = json.dumps([{"key": "sk-x", "user": "u"}])
    provider = build_auth_provider("api_key", {"raw": keys})
    assert isinstance(provider, ApiKeyAuthProvider)
    caller = await provider.authenticate(_request({"x-api-key": "sk-x"}))
    assert caller.user_id == "u"


async def test_build_composite_from_config() -> None:
    """The ``composite`` factory builds and orders its child providers from config."""
    provider = build_auth_provider(
        "composite",
        {
            "providers": [
                {"name": "forwarded", "config": {"trust_forwarded_from": ["agentgateway"]}},
                {"name": "api_key", "config": {"raw": json.dumps([{"key": "sk-x", "user": "u"}])}},
            ]
        },
    )
    assert isinstance(provider, CompositeAuthProvider)
    # A non-gateway request falls through forwarded → api_key.
    caller = await provider.authenticate(_request({"x-api-key": "sk-x"}))
    assert caller.user_id == "u"
    # A spoofed gateway marker is still rejected (recognized-but-invalid, no fall-through).
    with pytest.raises(AuthError) as excinfo:
        await provider.authenticate(_request({"x-forwarded-by": "evil", "x-api-key": "sk-x"}))
    assert excinfo.value.code == "untrusted_source"


def test_unknown_provider_fails_with_installed_names() -> None:
    """An unknown provider name raises an actionable error listing what is installed."""
    with pytest.raises(AgentShipError) as excinfo:
        build_auth_provider("saml", {})
    message = str(excinfo.value)
    assert "saml" in message
    assert "api_key" in message  # names the installed alternatives
