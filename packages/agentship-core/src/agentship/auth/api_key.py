"""The gateway-free dev/CI auth path: static API keys mapped to callers.

When there is no gateway forwarding identity (local development, CI, a single-box
deploy), a static API key is the simplest credential. :class:`ApiKeyAuthProvider` reads
a key off the request and looks it up in an :class:`ApiKeyStore`; :class:`EnvApiKeyStore`
loads its key table from an environment variable.

The store never keeps the raw keys — it hashes each with SHA-256 at load time and holds
only the digests, comparing a presented key's digest in constant time. So the key table
in memory (or a leaked process dump) exposes no usable credential, and a comparison never
leaks timing about how much of a wrong key was right.

The key table is JSON: a list of ``{"key", "user", "tenant"?, "scopes"?}`` objects, e.g.::

    [{"key": "sk-dev-abc", "tenant": "acme", "user": "u-1", "scopes": ["agent:*:invoke"]}]

``user`` is required; ``tenant`` defaults to ``"default"`` and ``scopes`` to none.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from abc import ABC, abstractmethod

from ..context import Caller
from ..errors import AgentShipError, AuthError
from . import AuthProvider, RequestLike

#: The environment variable :class:`EnvApiKeyStore` reads its JSON key table from.
DEFAULT_ENV_VAR = "AGENTSHIP_API_KEYS"


def _hash_key(key: str) -> str:
    """Return the SHA-256 hex digest of an API key (what the store stores/compares)."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


class ApiKeyStore(ABC):
    """Maps a presented API key to the :class:`Caller` it authenticates, or ``None``.

    Implementations must compare keys by hash in constant time and must not retain the
    raw keys. :meth:`lookup` is ``async`` so a future store can be backed by a database
    without changing the provider.
    """

    @abstractmethod
    async def lookup(self, presented_key: str) -> Caller | None:
        """Return the caller for ``presented_key``, or ``None`` if it is not a known key."""


class EnvApiKeyStore(ApiKeyStore):
    """An :class:`ApiKeyStore` whose key table comes from a JSON environment variable.

    At construction the table is parsed and each key is replaced by its SHA-256 digest,
    so the raw keys are never retained. Pass ``raw=`` to supply the JSON directly (used
    by tests and by callers that read the table themselves); otherwise the named
    environment variable is read (an unset/empty variable yields an empty store).
    """

    def __init__(self, env_var: str = DEFAULT_ENV_VAR, *, raw: str | None = None) -> None:
        """Build the hash→caller table from ``raw`` JSON or the ``env_var`` environment var."""
        source = raw if raw is not None else os.environ.get(env_var, "")
        self._by_hash: dict[str, Caller] = _parse_key_table(source)

    def _is_empty(self) -> bool:
        """Whether the store holds no keys (an unconfigured dev/CI environment)."""
        return not self._by_hash

    async def lookup(self, presented_key: str) -> Caller | None:
        """Return the caller a known key maps to, comparing digests in constant time."""
        digest = _hash_key(presented_key)
        for stored_hash, caller in self._by_hash.items():
            if hmac.compare_digest(stored_hash, digest):
                return caller
        return None


class ApiKeyAuthProvider(AuthProvider):
    """Authenticate a request by its API key against an :class:`ApiKeyStore`.

    The key is read from ``X-API-Key`` or from an ``Authorization: Bearer <key>`` header.
    A missing key is ``no_credentials``; a present-but-unknown key is ``invalid_api_key``.
    """

    def __init__(self, store: ApiKeyStore, *, api_key_header: str = "x-api-key") -> None:
        """Bind the key store and (optionally) the header the API key is read from."""
        self._store = store
        self._api_key_header = api_key_header

    async def authenticate(self, request: RequestLike) -> Caller:
        """Return the caller the presented API key maps to, or raise :class:`AuthError`."""
        key = _extract_key(request.headers, self._api_key_header)
        if key is None:
            raise AuthError(
                "no_credentials",
                f"no API key — send it in the {self._api_key_header!r} header or as "
                "'Authorization: Bearer <key>'",
            )
        caller = await self._store.lookup(key)
        if caller is None:
            raise AuthError("invalid_api_key", "the presented API key is not recognized")
        return caller


def _extract_key(headers, api_key_header: str) -> str | None:
    """Read the API key from the dedicated header or an ``Authorization: Bearer`` header.

    Returns ``None`` when neither carries a non-empty key, so the provider reports a
    clean ``no_credentials`` rather than looking up an empty string.
    """
    direct = headers.get(api_key_header)
    if direct:
        return direct.strip() or None
    authorization = headers.get("authorization", "")
    prefix = "Bearer "
    if authorization.startswith(prefix):
        return authorization[len(prefix) :].strip() or None
    return None


def _parse_key_table(source: str) -> dict[str, Caller]:
    """Parse the JSON key table into a ``{sha256(key): Caller}`` map.

    An empty/blank source is a valid empty table (unconfigured dev/CI). Any other
    parse or shape error raises :class:`~agentship.errors.AgentShipError` at construction
    so a typo in the key table fails fast rather than silently rejecting every request.
    """
    if not source.strip():
        return {}
    try:
        entries = json.loads(source)
    except json.JSONDecodeError as exc:
        raise AgentShipError(f"API key table is not valid JSON: {exc}") from exc
    if not isinstance(entries, list):
        raise AgentShipError("API key table must be a JSON list of key objects")

    table: dict[str, Caller] = {}
    for entry in entries:
        if not isinstance(entry, dict) or "key" not in entry or "user" not in entry:
            raise AgentShipError(
                "each API key entry needs at least 'key' and 'user' fields "
                f"(got {entry!r})"
            )
        caller = Caller(
            tenant_id=entry.get("tenant", "default"),
            user_id=entry["user"],
            scopes=set(entry.get("scopes", [])),
            auth_method="api_key",
        )
        table[_hash_key(entry["key"])] = caller
    return table
