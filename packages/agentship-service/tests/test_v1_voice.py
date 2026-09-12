"""The ``/voice`` WebSocket: voice as a capability of the service you already run.

These assert the contract a client depends on — who may open the socket, and what closing it
means — without needing a microphone, a provider key or a voice framework. The pipeline itself
is ``agentship-voice``'s and is tested there.
"""

from __future__ import annotations

import json

import agentship_service.routers.voice as voice_router
import pytest
from agentship.auth import ApiKeyAuthProvider, EnvApiKeyStore
from agentship.runtime import build_agent
from agentship.spec import AgentSpec
from agentship_service import AgentRegistry, create_app
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

#: ``full`` may invoke anything; ``narrow`` may invoke only ``support``.
_KEYS = json.dumps(
    [
        {"key": "full", "user": "u1", "tenant": "acme", "scopes": ["*"]},
        {"key": "narrow", "user": "u2", "tenant": "beta", "scopes": ["agent:support:invoke"]},
    ]
)

_VOICE_SPEC = {
    "name": "talker",
    "engine": "echo",
    "voice": {"stt": "openai", "tts": "openai"},
}


def _client() -> TestClient:
    """A client over a service with one voice agent and one text-only agent."""
    agents = AgentRegistry(
        [
            build_agent(AgentSpec.model_validate(_VOICE_SPEC)),
            build_agent(AgentSpec(name="support", engine="echo")),
        ]
    )
    auth = ApiKeyAuthProvider(EnvApiKeyStore(raw=_KEYS))
    return TestClient(create_app(auth=auth, agents=agents))


def test_an_agent_without_a_voice_block_cannot_be_talked_to() -> None:
    """A text-only agent closes 4404 — there is nothing to talk to, and that is not an error."""
    client = _client()
    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect("/v1/agents/support/voice", headers={"x-api-key": "full"}):
            pass
    assert closed.value.code == voice_router._CLOSE_NOT_VOICE


def test_a_caller_without_the_scope_is_refused() -> None:
    """Talking to an agent is invoking it, so it takes the same scope — 4403 without it."""
    client = _client()
    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect("/v1/agents/talker/voice", headers={"x-api-key": "narrow"}):
            pass
    assert closed.value.code == voice_router._CLOSE_FORBIDDEN


def test_an_unknown_agent_looks_exactly_like_a_forbidden_one() -> None:
    """A missing agent must not be distinguishable from one the caller may not reach.

    Different close codes here would let an unauthorised caller enumerate which agents a
    deployment runs, one socket at a time.
    """
    client = _client()
    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect("/v1/agents/ghost/voice", headers={"x-api-key": "full"}):
            pass
    assert closed.value.code == voice_router._CLOSE_FORBIDDEN


def test_a_deployment_without_the_voice_package_says_so(monkeypatch) -> None:
    """No voice package → 4503, not a crash and not a silent accept.

    The service must start and serve every text agent on a stack that has never heard of a
    voice framework; only this one endpoint is unavailable.
    """

    def _absent():
        raise ImportError("no agentship_voice")

    monkeypatch.setattr(voice_router, "_load_voice", _absent)
    client = _client()
    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect("/v1/agents/talker/voice", headers={"x-api-key": "full"}):
            pass
    assert closed.value.code == voice_router._CLOSE_UNAVAILABLE


def test_no_credential_is_refused_before_the_agent_is_resolved() -> None:
    """An unauthenticated handshake never reaches the route."""
    client = _client()
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/v1/agents/talker/voice"):
            pass


def test_a_browser_authenticates_with_the_bearer_subprotocol() -> None:
    """A browser cannot set WebSocket headers, so the credential rides the subprotocol.

    Without this Studio could not talk to its own service: ``new WebSocket(url, protocols)`` is
    the entire browser API, and it carries no way to add an Authorization header. Getting past
    auth to the route's own 4404 is the proof — the handshake was authenticated.
    """
    client = _client()
    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect("/v1/agents/support/voice", subprotocols=["bearer", "full"]):
            pass
    assert closed.value.code == voice_router._CLOSE_NOT_VOICE, "auth passed; the route answered"


def test_a_bad_token_in_the_subprotocol_is_still_refused() -> None:
    """The subprotocol is a transport for the credential, not a way around checking it."""
    client = _client()
    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect(
            "/v1/agents/talker/voice", subprotocols=["bearer", "not-a-key"]
        ):
            pass
    assert closed.value.code == 4401


def test_an_explicit_header_wins_over_the_subprotocol() -> None:
    """A real header is the stronger signal and must not be overridden by a handshake list."""
    client = _client()
    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect(
            "/v1/agents/talker/voice",
            headers={"x-api-key": "narrow"},
            subprotocols=["bearer", "full"],
        ):
            pass
    assert closed.value.code == voice_router._CLOSE_FORBIDDEN, "the header's caller was used"
