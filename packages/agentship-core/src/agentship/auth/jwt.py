"""The optional gateway-free OIDC path: validate a bearer JWT in the app.

Behind agentgateway the gateway validates tokens and forwards identity, so this adapter
is **not** the default path — it is a hedge for deployments that want to accept OIDC
tokens without a gateway. It validates a bearer JWT (signature, issuer, audience, expiry,
not-before) and maps configurable claims to a :class:`~agentship.context.Caller`.

Two key modes:

* **static key** — an RSA/EC public key (or HMAC secret) supplied directly; and
* **JWKS URL** — the provider fetches the issuer's key set and caches it by key id with a
  TTL (:class:`JwksCache`), refreshing on an unknown ``kid`` so key rotation is handled
  without a per-request network call.

This module imports PyJWT lazily inside its constructors, so ``from agentship.auth.jwt
import JwtAuthProvider`` works in a base install; constructing a provider without the
``[jwt]`` extra installed raises an actionable error instead of an ``ImportError`` at import.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from ..context import Caller
from ..errors import AuthError
from . import AuthProvider, RequestLike

#: A JWKS fetcher: an async callable returning the parsed JWKS document (``{"keys": [...]}``).
JwksFetcher = Callable[[], Awaitable[dict]]


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


class JwksCache:
    """Fetch and cache an issuer's JWKS, resolving a signing key by its ``kid``.

    Keys are cached until the TTL elapses; a lookup for an unknown ``kid`` forces one
    refresh (handling issuer key rotation) before giving up. The network fetch and the
    clock are both injectable so the cache is unit-testable without touching the network.
    """

    def __init__(
        self,
        jwks_url: str,
        *,
        ttl_seconds: float = 3600.0,
        fetcher: JwksFetcher | None = None,
        now: Callable[[], float] | None = None,
    ) -> None:
        """Configure the JWKS URL, cache TTL, and (optionally) the fetcher and clock."""
        self._url = jwks_url
        self._ttl = ttl_seconds
        self._fetch = fetcher or self._default_fetcher
        self._now = now or _monotonic
        self._keys: dict[str, object] = {}
        self._fetched_at: float | None = None

    async def public_key_for(self, kid: str) -> object:
        """Return the public key for ``kid``, refreshing the cache if stale or on a miss.

        Raises :class:`~agentship.errors.AuthError` (``invalid_token``) if the key id is
        still unknown after a refresh — a token referencing a key the issuer does not
        publish cannot be trusted.
        """
        if self._is_stale() or kid not in self._keys:
            await self._refresh()
        key = self._keys.get(kid)
        if key is None:
            raise AuthError("invalid_token", f"token references unknown signing key id {kid!r}")
        return key

    def _is_stale(self) -> bool:
        """Whether the cache has never been filled or its TTL has elapsed."""
        return self._fetched_at is None or (self._now() - self._fetched_at) >= self._ttl

    async def _refresh(self) -> None:
        """Re-fetch the JWKS and rebuild the ``kid`` → public-key map."""
        jwt = _require_pyjwt()
        document = await self._fetch()
        keys: dict[str, object] = {}
        for jwk in document.get("keys", []):
            kid = jwk.get("kid")
            if kid is None:
                continue
            keys[kid] = jwt.PyJWK(jwk).key
        self._keys = keys
        self._fetched_at = self._now()

    async def _default_fetcher(self) -> dict:  # pragma: no cover - real network, integration only
        """Fetch the JWKS document over HTTP (used when no fetcher is injected)."""
        import httpx

        async with httpx.AsyncClient() as client:
            response = await client.get(self._url, timeout=5.0)
            response.raise_for_status()
            return response.json()


class JwtAuthProvider(AuthProvider):
    """Authenticate a request by validating its bearer JWT.

    Configure exactly one key source: a static ``public_key`` (PEM string, key object, or
    HMAC secret) **or** a ``jwks_url``. Claims are mapped by name: ``user_claim`` (default
    ``sub``), ``tenant_claim``, and ``scopes_claim`` (a space-delimited string or a list).
    """

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        public_key: object | None = None,
        jwks_url: str | None = None,
        jwks_fetcher: JwksFetcher | None = None,
        jwks_ttl_seconds: float = 3600.0,
        jwks_now: Callable[[], float] | None = None,
        algorithms: tuple[str, ...] = ("RS256",),
        user_claim: str = "sub",
        tenant_claim: str = "tenant",
        scopes_claim: str = "scope",
    ) -> None:
        """Validate the configuration and set up the chosen key source (static or JWKS)."""
        _require_pyjwt()  # fail fast now if the extra is missing, not on first request
        if (public_key is None) == (jwks_url is None):
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
        self._jwks = (
            JwksCache(
                jwks_url, ttl_seconds=jwks_ttl_seconds, fetcher=jwks_fetcher, now=jwks_now
            )
            if jwks_url is not None
            else None
        )

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
        """Return the verification key: the static key, or the JWKS key for the token's kid."""
        if self._public_key is not None:
            return self._public_key
        assert self._jwks is not None  # guaranteed by the constructor's xor check
        try:
            kid = jwt.get_unverified_header(token).get("kid")
        except jwt.InvalidTokenError as exc:
            raise AuthError("invalid_token", "malformed JWT header") from exc
        if kid is None:
            raise AuthError("invalid_token", "JWKS mode requires a 'kid' in the JWT header")
        return await self._jwks.public_key_for(kid)

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


def _monotonic() -> float:
    """Default clock for the JWKS cache TTL (monotonic so it is immune to clock steps)."""
    import time

    return time.monotonic()
