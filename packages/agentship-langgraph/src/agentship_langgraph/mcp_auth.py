"""Encrypted, persistent OAuth token storage for remote MCP servers (Phase 03 · C3.3).

The ``mcp`` SDK's ``OAuthClientProvider`` runs the OAuth 2.1 + PKCE flow (discovery → dynamic client
registration → authorize → token exchange → refresh); the one piece it does **not** provide is where
tokens live across restarts — that is the app's job, via the SDK's ``TokenStorage`` protocol.
:class:`EncryptedTokenStorage` implements it over a pluggable key-value store, Fernet-encrypting the
tokens and the registered client info at rest so a remote MCP server is not re-authorized every
process start. This is the carry-forward of the old repo's encrypted-DB token store (no regression);
only this thin persistence layer is ours — the flow itself comes from the SDK.
"""

from __future__ import annotations

from typing import Protocol, TypeVar

from cryptography.fernet import Fernet
from mcp.client.auth import TokenStorage
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

_Model = TypeVar("_Model", OAuthToken, OAuthClientInformationFull)


class TokenStore(Protocol):
    """A tiny key → bytes store the encrypted token storage persists into (a DB in production)."""

    def get(self, key: str) -> bytes | None:
        """Return the stored bytes for ``key``, or ``None`` if absent."""
        ...

    def set(self, key: str, value: bytes) -> None:
        """Persist ``value`` under ``key``."""
        ...


class DictTokenStore:
    """An in-memory :class:`TokenStore` (the default; swap a DB-backed one in production)."""

    def __init__(self) -> None:
        """Start with an empty in-memory map."""
        self._entries: dict[str, bytes] = {}

    def get(self, key: str) -> bytes | None:
        """Return the stored bytes for ``key`` if present."""
        return self._entries.get(key)

    def set(self, key: str, value: bytes) -> None:
        """Store ``value`` under ``key``."""
        self._entries[key] = value


class EncryptedTokenStorage(TokenStorage):
    """An ``mcp`` SDK :class:`TokenStorage` that Fernet-encrypts tokens/client-info in a store.

    One instance serves one MCP ``server`` (keys are namespaced by server name, so servers sharing a
    store stay isolated). ``key`` is a Fernet key; ``store`` is any :class:`TokenStore`.
    """

    def __init__(self, *, key: bytes, store: TokenStore, server: str) -> None:
        """Bind the Fernet ``key``, the backing ``store``, and the owning MCP ``server`` name."""
        self._fernet = Fernet(key)
        self._store = store
        self._server = server

    def _key(self, kind: str) -> str:
        """Namespace a storage key by server + kind (``tokens`` or ``client``)."""
        return f"mcp:{self._server}:{kind}"

    def _write(self, kind: str, model: _Model) -> None:
        """Serialize + encrypt ``model`` and persist it under the server's ``kind`` key."""
        self._store.set(self._key(kind), self._fernet.encrypt(model.model_dump_json().encode()))

    def _read(self, kind: str, model_cls: type[_Model]) -> _Model | None:
        """Read + decrypt the server's ``kind`` entry into ``model_cls``, or ``None`` if absent."""
        blob = self._store.get(self._key(kind))
        if blob is None:
            return None
        return model_cls.model_validate_json(self._fernet.decrypt(blob))

    async def get_tokens(self) -> OAuthToken | None:
        """Return the persisted OAuth tokens for this server, or ``None``."""
        return self._read("tokens", OAuthToken)

    async def set_tokens(self, tokens: OAuthToken) -> None:
        """Persist the OAuth tokens for this server (encrypted)."""
        self._write("tokens", tokens)

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        """Return the persisted dynamically-registered client info, or ``None``."""
        return self._read("client", OAuthClientInformationFull)

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        """Persist the registered client info (so DCR is not re-run each restart)."""
        self._write("client", client_info)
