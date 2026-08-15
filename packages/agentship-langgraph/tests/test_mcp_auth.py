"""Phase 03 · C3.3 — encrypted, persistent OAuth token storage for remote MCP servers.

The ``mcp`` SDK runs the OAuth 2.1 + PKCE flow for us; the one piece we own is **persistence** — so
a remote MCP server's tokens (and its dynamically-registered client info) survive a process restart
without re-running the flow, encrypted at rest. This is where the old repo's encrypted-DB token store
maps in (no OAuth-persistence regression). These tests pin the round-trip + encryption + restart
survival of :class:`EncryptedTokenStorage`; the interactive browser flow itself is manual.
"""

from __future__ import annotations

import pytest

pytest.importorskip("mcp", reason="needs agentship-langgraph[mcp]")

from agentship_langgraph.mcp_auth import DictTokenStore, EncryptedTokenStorage  # noqa: E402
from cryptography.fernet import Fernet  # noqa: E402
from mcp.shared.auth import OAuthToken  # noqa: E402


async def test_tokens_round_trip_encrypted_and_survive_restart():
    """Tokens set through the storage read back equal, are encrypted at rest, and persist."""
    key = Fernet.generate_key()
    store = DictTokenStore()
    storage = EncryptedTokenStorage(key=key, store=store, server="github")

    await storage.set_tokens(OAuthToken(access_token="super-secret-token", token_type="Bearer"))
    assert (await storage.get_tokens()).access_token == "super-secret-token"

    # Encrypted at rest: the raw stored bytes never contain the plaintext secret.
    raw = store.get("mcp:github:tokens")
    assert raw is not None and b"super-secret-token" not in raw

    # Survives a restart: a fresh storage over the SAME store + key reads the persisted tokens.
    restarted = EncryptedTokenStorage(key=key, store=store, server="github")
    assert (await restarted.get_tokens()).access_token == "super-secret-token"


async def test_absent_tokens_return_none():
    """A server with no stored tokens yet returns None (not a crash)."""
    storage = EncryptedTokenStorage(key=Fernet.generate_key(), store=DictTokenStore(), server="x")
    assert await storage.get_tokens() is None
    assert await storage.get_client_info() is None


async def test_servers_are_isolated_by_name():
    """Two servers sharing a store do not read each other's tokens."""
    key = Fernet.generate_key()
    store = DictTokenStore()
    a = EncryptedTokenStorage(key=key, store=store, server="a")
    b = EncryptedTokenStorage(key=key, store=store, server="b")
    await a.set_tokens(OAuthToken(access_token="a-token", token_type="Bearer"))
    assert await b.get_tokens() is None
