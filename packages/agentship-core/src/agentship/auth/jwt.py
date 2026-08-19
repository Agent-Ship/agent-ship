"""The optional gateway-free OIDC path: validate a bearer JWT in the app.

Behind agentgateway the gateway validates tokens and forwards identity, so this adapter
is **not** the default path — it is a hedge for deployments that want to accept OIDC
tokens without a gateway. It validates a bearer JWT (signature, issuer, audience, expiry,
not-before) and maps configurable claims to a :class:`~agentship.context.Caller`.

Two key modes:

* **static key** — an RSA/EC public key (or HMAC secret) supplied directly; and
* **JWKS URL** — PyJWT's :class:`jwt.PyJWKClient` fetches the issuer's key set and caches
  it, resolving the signing key for a token's ``kid`` and refreshing on rotation. We do
  not reimplement JWKS caching — the client is the integration point.

This module imports PyJWT lazily inside its constructors, so ``from agentship.auth.jwt
import JwtAuthProvider`` works in a base install; constructing a provider without the
``[jwt]`` extra installed raises an actionable error instead of an ``ImportError`` at import.
"""

from __future__ import annotations

import asyncio

from ..context import Caller
from ..errors import AuthError
from . import AuthProvider, RequestLike


def _require_pyjwt():
    """Import and return the PyJWT module, or raise an actionable error if it is absent."""
    try:
        import jwt  # PyJWT (absolute import; not this module)

        return jwt
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise ValueError(
            "JwtAuthProvider needs PyJWT — install the optional extra: "
            "pip install 'agentship-core[jwt]'"
        ) from exc


class JwtAuthProvider(AuthProvider):
    """Authenticate a request by validating its bearer JWT.

    Configure exactly one key source: a static ``public_key`` (PEM string, key object, or
    HMAC secret) **or** a ``jwks_url`` (backed by PyJWT's :class:`jwt.PyJWKClient`). Claims
    are mapped by name: ``user_claim`` (default ``sub``), ``tenant_claim``, and
    ``scopes_claim`` (a space-delimited string or a list).
    """

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        public_key: object | None = None,
        jwks_url: str | None = None,
        jwks_ttl_seconds: float = 3600.0,
        jwks_client: object | None = None,
        algorithms: tuple[str, ...] = ("RS256",),
        user_claim: str = "sub",
        tenant_claim: str = "tenant",
        scopes_claim: str = "scope",
    ) -> None:
        """Validate the configuration and set up the chosen key source (static or JWKS).

        ``jwks_client`` is an injection seam for tests: pass a pre-built
        :class:`jwt.PyJWKClient` (or any object exposing ``get_signing_key_from_jwt``) to
        avoid a live network fetch. In production only ``jwks_url`` is supplied and the
        client is constructed here.
        """
        jwt = _require_pyjwt()  # fail fast now if the extra is missing, not on first request
        has_static = public_key is not None
        has_jwks = jwks_url is not None or jwks_client is not None
        if has_static == has_jwks:
            raise ValueError(
                "JwtAuthProvider needs exactly one key source: pass either public_key "
                "(static key) or jwks_url (fetched key set), not both or neither"
            )
        self._issuer = issuer
        self._audience = audience
        self._public_key = public_key
        self._algorithms = list(algorithms)
        self._user_claim = user_claim
        self._tenant_claim = tenant_claim
        self._scopes_claim = scopes_claim
        if jwks_client is not None:
            self._jwks_client = jwks_client
        elif jwks_url is not None:
            # PyJWKClient owns the fetch, kid→key resolution, and TTL cache. lifespan is
            # its cache TTL in seconds; cache_keys keeps resolved keys across requests.
            self._jwks_client = jwt.PyJWKClient(
                jwks_url, cache_keys=True, lifespan=int(jwks_ttl_seconds)
            )
        else:
            self._jwks_client = None

    async def authenticate(self, request: RequestLike) -> Caller:
        """Validate the bearer JWT and map its claims to a :class:`Caller`."""
        jwt = _require_pyjwt()
        token = _extract_bearer(request.headers)
        if token is None:
            raise AuthError("no_credentials", "no bearer JWT in the Authorization header")

        key = await self._resolve_key(jwt, token)
        try:
            payload = jwt.decode(
                token,
                key,
                algorithms=self._algorithms,
                audience=self._audience,
                issuer=self._issuer,
                options={"require": ["exp"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise AuthError("expired_token", "the JWT 'exp' is in the past") from exc
        except jwt.InvalidTokenError as exc:
            # Covers a bad signature, wrong issuer/audience, not-yet-valid, or malformed
            # token — all "we cannot trust this token", surfaced under one code.
            raise AuthError("invalid_token", f"JWT validation failed: {exc}") from exc

        return self._caller_from(payload)

    async def _resolve_key(self, jwt, token: str) -> object:
        """Return the verification key: the static key, or the JWKS key for the token's kid.

        The synchronous :class:`jwt.PyJWKClient` (urllib under the hood, plus its own cache)
        is run in a worker thread so a cache-miss fetch never blocks the event loop. It
        reads the token's ``kid`` and resolves the matching signing key itself.
        """
        if self._public_key is not None:
            return self._public_key
        assert self._jwks_client is not None  # guaranteed by the constructor's xor check
        try:
            signing_key = await asyncio.to_thread(
                self._jwks_client.get_signing_key_from_jwt, token
            )
        except jwt.PyJWTError as exc:
            # Unknown kid, unreachable/invalid JWKS, or a malformed token header — none of
            # these yield a trustworthy key, so surface them under the one invalid_token code.
            raise AuthError("invalid_token", f"could not resolve JWT signing key: {exc}") from exc
        return signing_key.key

    def _caller_from(self, payload: dict) -> Caller:
        """Map a validated JWT payload to a :class:`Caller` using the configured claims."""
        user_id = payload.get(self._user_claim)
        if not user_id:
            raise AuthError(
                "invalid_token", f"JWT is missing the user claim {self._user_claim!r}"
            )
        tenant_id = payload.get(self._tenant_claim) or "default"
        scopes = _parse_scopes(payload.get(self._scopes_claim))
        return Caller(
            tenant_id=str(tenant_id), user_id=str(user_id), scopes=scopes, auth_method="jwt"
        )


def _extract_bearer(headers) -> str | None:
    """Return the token from an ``Authorization: Bearer <jwt>`` header, or ``None``."""
    authorization = headers.get("authorization", "")
    prefix = "Bearer "
    if authorization.startswith(prefix):
        return authorization[len(prefix) :].strip() or None
    return None


def _parse_scopes(raw: object) -> frozenset[str]:
    """Normalize a scopes claim (space-delimited string or list) into a frozenset."""
    if raw is None:
        return frozenset()
    if isinstance(raw, str):
        return frozenset(part for part in raw.split() if part)
    if isinstance(raw, (list, tuple)):
        return frozenset(str(part) for part in raw)
    return frozenset()
