"""Phase 04 · C2 — the optional gateway-free OIDC path: :class:`JwtAuthProvider`.

This adapter is a hedge for deployments that want to validate OIDC tokens *in the app*
without a gateway. It validates a bearer JWT's signature, issuer, audience, and
expiry/not-before, then maps configurable claims to a :class:`~agentship.context.Caller`.
It runs in two key modes — a static public key, or a JWKS URL whose keys are cached by
key id with a TTL and refreshed on an unknown ``kid`` (rotation). All tests here mock the
JWKS fetch, so nothing touches the network on the CI gate.
"""

from __future__ import annotations

import json
import time

import jwt
import pytest
from agentship.auth import AuthProvider
from agentship.auth.jwt import JwksCache, JwtAuthProvider
from agentship.errors import AuthError
from cryptography.hazmat.primitives.asymmetric import rsa

_ISSUER = "https://issuer.example"
_AUDIENCE = "agentship"


def _keypair():
    """Generate a throwaway RSA keypair for signing/verifying test tokens."""
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private, private.public_key()


_PRIVATE, _PUBLIC = _keypair()


def _jwk(public_key, kid: str) -> dict:
    """Serialize an RSA public key as a JWK dict carrying ``kid``."""
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(public_key))
    jwk["kid"] = kid
    return jwk


def _token(claims: dict, *, kid: str = "k1", key=_PRIVATE, alg: str = "RS256") -> str:
    """Sign a JWT with sensible issuer/audience/expiry defaults, overridable via claims."""
    payload = {
        "iss": _ISSUER,
        "aud": _AUDIENCE,
        "sub": "u-1",
        "exp": int(time.time()) + 300,
        **claims,
    }
    return jwt.encode(payload, key, algorithm=alg, headers={"kid": kid})


def _request(headers: dict[str, str]):
    class _Req:
        pass

    req = _Req()
    req.headers = headers
    return req


def _static_provider(**overrides) -> JwtAuthProvider:
    """A provider validating against the static test public key."""
    kwargs = {
        "issuer": _ISSUER,
        "audience": _AUDIENCE,
        "public_key": _PUBLIC,
        "tenant_claim": "tenant",
        "scopes_claim": "scope",
    }
    kwargs.update(overrides)
    return JwtAuthProvider(**kwargs)


def test_is_an_auth_provider() -> None:
    """The JWT provider is a concrete :class:`AuthProvider`."""
    assert isinstance(_static_provider(), AuthProvider)


def test_requires_a_key_source() -> None:
    """Construction fails fast if neither a static key nor a JWKS URL is configured."""
    with pytest.raises(ValueError, match="public_key|jwks_url"):
        JwtAuthProvider(issuer=_ISSUER, audience=_AUDIENCE)


async def test_valid_token_maps_claims_to_caller() -> None:
    """A well-signed token maps its tenant/sub/scope claims to a Caller."""
    token = _token({"tenant": "acme", "scope": "agent:support:invoke agent:support:stream"})
    caller = await _static_provider().authenticate(
        _request({"authorization": f"Bearer {token}"})
    )
    assert caller.tenant_id == "acme"
    assert caller.user_id == "u-1"
    assert caller.scopes == frozenset({"agent:support:invoke", "agent:support:stream"})
    assert caller.auth_method == "jwt"


async def test_scope_claim_may_be_a_list() -> None:
    """The scopes claim is accepted as a JSON array as well as a space-delimited string."""
    token = _token({"scope": ["a:b:c", "d:e:f"]})
    caller = await _static_provider().authenticate(
        _request({"authorization": f"Bearer {token}"})
    )
    assert caller.scopes == frozenset({"a:b:c", "d:e:f"})


async def test_missing_bearer_is_no_credentials() -> None:
    """No bearer token → ``no_credentials`` (falls through in a composite)."""
    with pytest.raises(AuthError) as excinfo:
        await _static_provider().authenticate(_request({}))
    assert excinfo.value.code == "no_credentials"


async def test_expired_token_is_expired_token() -> None:
    """An expired token gets the distinct ``expired_token`` code."""
    token = _token({"exp": int(time.time()) - 10})
    with pytest.raises(AuthError) as excinfo:
        await _static_provider().authenticate(_request({"authorization": f"Bearer {token}"}))
    assert excinfo.value.code == "expired_token"


async def test_wrong_audience_is_invalid_token() -> None:
    """A token minted for another audience is rejected as ``invalid_token``."""
    token = _token({"aud": "someone-else"})
    with pytest.raises(AuthError) as excinfo:
        await _static_provider().authenticate(_request({"authorization": f"Bearer {token}"}))
    assert excinfo.value.code == "invalid_token"


async def test_wrong_issuer_is_invalid_token() -> None:
    """A token from an unexpected issuer is rejected as ``invalid_token``."""
    token = _token({"iss": "https://evil.example"})
    with pytest.raises(AuthError) as excinfo:
        await _static_provider().authenticate(_request({"authorization": f"Bearer {token}"}))
    assert excinfo.value.code == "invalid_token"


async def test_bad_signature_is_invalid_token() -> None:
    """A token signed by a different key fails signature validation."""
    other_private, _ = _keypair()
    token = _token({}, key=other_private)
    with pytest.raises(AuthError) as excinfo:
        await _static_provider().authenticate(_request({"authorization": f"Bearer {token}"}))
    assert excinfo.value.code == "invalid_token"


# ---- JWKS cache (unit tier: a mocked fetcher, no network) --------------------------


async def test_jwks_mode_validates_via_fetched_key() -> None:
    """In JWKS mode the provider resolves the signing key from the fetched key set."""
    calls = {"n": 0}

    async def fetch() -> dict:
        calls["n"] += 1
        return {"keys": [_jwk(_PUBLIC, "k1")]}

    provider = JwtAuthProvider(
        issuer=_ISSUER,
        audience=_AUDIENCE,
        jwks_url="https://issuer.example/jwks",
        jwks_fetcher=fetch,
        scopes_claim="scope",
    )
    token = _token({"scope": "x:y:z"}, kid="k1")
    caller = await provider.authenticate(_request({"authorization": f"Bearer {token}"}))
    assert caller.scopes == frozenset({"x:y:z"})
    assert calls["n"] == 1


async def test_jwks_cache_serves_within_ttl_and_refetches_after() -> None:
    """Keys are cached within the TTL and re-fetched only once it has elapsed."""
    clock = {"t": 1000.0}
    calls = {"n": 0}

    async def fetch() -> dict:
        calls["n"] += 1
        return {"keys": [_jwk(_PUBLIC, "k1")]}

    cache = JwksCache(
        "https://issuer.example/jwks",
        ttl_seconds=60,
        fetcher=fetch,
        now=lambda: clock["t"],
    )
    await cache.public_key_for("k1")
    await cache.public_key_for("k1")
    assert calls["n"] == 1  # second lookup served from cache

    clock["t"] += 61  # TTL elapsed
    await cache.public_key_for("k1")
    assert calls["n"] == 2  # refetched


async def test_jwks_cache_refetches_on_unknown_kid() -> None:
    """An unknown key id triggers a refetch (key rotation) rather than failing blind."""
    keys = {"current": "k1"}
    calls = {"n": 0}

    async def fetch() -> dict:
        calls["n"] += 1
        return {"keys": [_jwk(_PUBLIC, keys["current"])]}

    cache = JwksCache(
        "https://issuer.example/jwks", ttl_seconds=3600, fetcher=fetch, now=lambda: 0.0
    )
    await cache.public_key_for("k1")
    assert calls["n"] == 1

    keys["current"] = "k2"  # issuer rotated its signing key
    await cache.public_key_for("k2")  # unknown kid → refetch even though TTL is fresh
    assert calls["n"] == 2


async def test_jwks_cache_unknown_kid_after_refetch_is_invalid_token() -> None:
    """A kid still absent after a refetch is a hard ``invalid_token``."""

    async def fetch() -> dict:
        return {"keys": [_jwk(_PUBLIC, "k1")]}

    cache = JwksCache(
        "https://issuer.example/jwks", ttl_seconds=3600, fetcher=fetch, now=lambda: 0.0
    )
    with pytest.raises(AuthError) as excinfo:
        await cache.public_key_for("nope")
    assert excinfo.value.code == "invalid_token"
