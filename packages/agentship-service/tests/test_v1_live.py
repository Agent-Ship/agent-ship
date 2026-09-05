"""The ``/live`` WebSocket: streaming a turn, scope/close-code contract, and barge-in.

The echo engine streams a ``content`` frame then ``done``, so a turn over the socket is
deterministic. These assert the happy path (session → content → done), the close codes for
an unauthorized/forbidden handshake, and that sending a second turn cancels the first
(barge-in) — the property a live UI needs.
"""

from __future__ import annotations

import json

import pytest
from agentship.auth import ApiKeyAuthProvider, EnvApiKeyStore
from agentship.runtime import build_agent
from agentship.spec import AgentSpec
from agentship_service import AgentRegistry, create_app
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

_KEYS = json.dumps(
    [
        {"key": "full", "user": "u1", "tenant": "acme", "scopes": ["*"]},
        {"key": "narrow", "user": "u2", "tenant": "beta", "scopes": ["agent:other:invoke"]},
    ]
)


def _client() -> TestClient:
    """Build a client over an app serving one streaming ``support`` agent."""
    agents = AgentRegistry([build_agent(AgentSpec(name="support", engine="echo", streaming=True))])
    auth = ApiKeyAuthProvider(EnvApiKeyStore(raw=_KEYS))
    return TestClient(create_app(auth=auth, agents=agents))


def test_live_streams_a_turn() -> None:
    """A scoped caller sends a turn and receives session → content → done frames."""
    client = _client()
    with client.websocket_connect("/v1/agents/support/live", headers={"x-api-key": "full"}) as ws:
        ws.send_json({"input": "hello"})
        first = ws.receive_json()
        assert first["type"] == "session"
        assert first["data"]["agent"] == "support"

        frames = [first]
        while frames[-1]["type"] != "done":
            frames.append(ws.receive_json())

    assert [f["seq"] for f in frames] == list(range(len(frames)))
    assert any(f["type"] == "content" for f in frames)


def test_live_barge_in_starts_a_fresh_turn() -> None:
    """A second turn sent mid-conversation opens a new session and streams again."""
    client = _client()
    with client.websocket_connect("/v1/agents/support/live", headers={"x-api-key": "full"}) as ws:
        ws.send_json({"input": "first", "session_id": "s1"})
        assert ws.receive_json()["data"]["session_id"] == "s1"

        # Barge in with a new turn; the next session frame is the new turn's.
        ws.send_json({"input": "second", "session_id": "s2"})
        # Drain frames until the s2 session frame appears (the first turn may still have
        # in-flight frames queued ahead of it).
        seen_s2 = False
        for _ in range(20):
            frame = ws.receive_json()
            if frame["type"] == "session" and frame["data"]["session_id"] == "s2":
                seen_s2 = True
                break
        assert seen_s2


def test_live_unauthenticated_handshake_is_rejected() -> None:
    """No credential → the handshake is closed (4401) by the auth middleware."""
    client = _client()
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect("/v1/agents/support/live"):
            pass
    assert excinfo.value.code == 4401


def test_live_forbidden_scope_closes_4403() -> None:
    """An authenticated caller without invoke scope is closed with 4403."""
    client = _client()
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect("/v1/agents/support/live", headers={"x-api-key": "narrow"}):
            pass
    assert excinfo.value.code == 4403


def test_live_unknown_agent_closes_4403() -> None:
    """An unknown agent is closed 4403 too, so the socket never reveals its absence."""
    client = _client()
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect("/v1/agents/ghost/live", headers={"x-api-key": "full"}):
            pass
    assert excinfo.value.code == 4403
