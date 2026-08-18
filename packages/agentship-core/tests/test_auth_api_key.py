"""Phase 04 · C2 — the gateway-free dev/CI auth path: :class:`ApiKeyAuthProvider`.

For local development and CI (where there is no gateway forwarding identity), a static
API key is enough. The store keeps only SHA-256 hashes of the keys — never the raw keys
— and compares in constant time, so a leaked process image or log never exposes a usable
credential. A valid key maps to a :class:`~agentship.context.Caller`; anything else is an
:class:`~agentship.errors.AuthError`.
"""

from __future__ import annotations

import json

import pytest
from agentship.auth import ApiKeyAuthProvider, AuthProvider, EnvApiKeyStore
from agentship.context import Caller
from agentship.errors import AgentShipError, AuthError

# A tiny key table: one full-scoped admin key and one narrowly-scoped support key.
_KEYS = [
    {"key": "sk-admin", "tenant": "acme", "user": "admin", "scopes": ["*"]},
    {
        "key": "sk-support",
        "tenant": "acme",
        "user": "agent-7",
        "scopes": ["agent:support:invoke"],
    },
    {"key": "sk-bare", "user": "solo"},  # no tenant/scopes → defaults
]


def _request(headers: dict[str, str]):
    """A minimal RequestLike exposing a ``headers`` mapping."""

    class _Req:
        pass

    req = _Req()
    req.headers = headers
    return req


def _provider() -> ApiKeyAuthProvider:
    """A provider over an in-memory store built from the ``_KEYS`` table."""
    return ApiKeyAuthProvider(EnvApiKeyStore(raw=json.dumps(_KEYS)))


def test_is_an_auth_provider() -> None:
    """The API-key provider is a concrete :class:`AuthProvider`."""
    assert isinstance(_provider(), AuthProvider)


def test_store_keeps_no_plaintext_keys() -> None:
    """The store retains SHA-256 hashes, never the raw key strings."""
    store = EnvApiKeyStore(raw=json.dumps(_KEYS))
    blob = repr(vars(store))
    assert "sk-admin" not in blob
    assert "sk-support" not in blob


async def test_valid_key_via_x_api_key_header() -> None:
    """A known key in ``X-API-Key`` authenticates to its mapped caller."""
    caller = await _provider().authenticate(_request({"x-api-key": "sk-admin"}))
    assert caller == Caller(
        tenant_id="acme", user_id="admin", scopes={"*"}, auth_method="api_key"
    )


async def test_valid_key_via_authorization_bearer() -> None:
    """A known key presented as ``Authorization: Bearer <key>`` also works."""
    caller = await _provider().authenticate(
        _request({"authorization": "Bearer sk-support"})
    )
    assert caller.user_id == "agent-7"
    assert caller.scopes == frozenset({"agent:support:invoke"})
    assert caller.auth_method == "api_key"


async def test_key_without_tenant_or_scopes_uses_defaults() -> None:
    """An entry omitting tenant/scopes yields the single-tenant default and no scopes."""
    caller = await _provider().authenticate(_request({"x-api-key": "sk-bare"}))
    assert caller.tenant_id == "default"
    assert caller.scopes == frozenset()


async def test_unknown_key_is_invalid_api_key() -> None:
    """An unknown key raises ``AuthError('invalid_api_key')`` — never a silent caller."""
    with pytest.raises(AuthError) as excinfo:
        await _provider().authenticate(_request({"x-api-key": "sk-nope"}))
    assert excinfo.value.code == "invalid_api_key"


async def test_missing_key_is_no_credentials() -> None:
    """A request carrying no API key at all raises ``AuthError('no_credentials')``."""
    with pytest.raises(AuthError) as excinfo:
        await _provider().authenticate(_request({}))
    assert excinfo.value.code == "no_credentials"


async def test_empty_bearer_is_no_credentials() -> None:
    """An ``Authorization: Bearer`` with no token is treated as no credentials."""
    with pytest.raises(AuthError) as excinfo:
        await _provider().authenticate(_request({"authorization": "Bearer "}))
    assert excinfo.value.code == "no_credentials"


def test_malformed_key_table_fails_fast() -> None:
    """A non-JSON or structurally-invalid key table is refused at construction."""
    with pytest.raises(AgentShipError):
        EnvApiKeyStore(raw="not json{")
    with pytest.raises(AgentShipError):
        EnvApiKeyStore(raw=json.dumps([{"key": "k"}]))  # missing required user


def test_env_var_is_read_when_no_raw_given(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no ``raw=`` the store reads its key table from the named environment var."""
    monkeypatch.setenv("AGENTSHIP_API_KEYS", json.dumps(_KEYS))
    store = EnvApiKeyStore()
    # An unset/empty env var yields an empty store rather than an error.
    monkeypatch.delenv("AGENTSHIP_API_KEYS")
    assert EnvApiKeyStore()._is_empty()
    assert not store._is_empty()
