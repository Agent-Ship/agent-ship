"""The A2A server surface mounted on the runtime app: cards, message/send, message/stream (§C4).

Only agents that opt in (``a2a.expose: true``) get a card and an endpoint; the rest are 404 on
the network. Auth, TLS, and rate-limit are inherited from the P04 app because these routes live on
the same FastAPI instance — so the A2A path is guarded exactly like ``/v1``.
"""

from __future__ import annotations

import json

from agentship.auth import ApiKeyAuthProvider, EnvApiKeyStore
from agentship.runtime import build_agent
from agentship.spec import AgentSpec
from agentship_service import AgentRegistry, create_app
from fastapi.testclient import TestClient

#: ``full`` may A2A-invoke any agent; ``narrow`` holds only direct invoke on ``triage`` (not the
#: distinct ``a2a:invoke`` verb) so we can prove cross-agent A2A needs its own grant.
_KEYS = json.dumps(
    [
        {"key": "full", "user": "u1", "tenant": "acme", "scopes": ["*"]},
        {"key": "narrow", "user": "u2", "tenant": "beta", "scopes": ["agent:triage:invoke"]},
    ]
)


def _client() -> TestClient:
    """App serving an exposed ``triage`` agent and a non-exposed ``billing`` agent."""
    agents = AgentRegistry(
        [
            build_agent(
                AgentSpec(
                    name="triage",
                    engine="echo",
                    streaming=True,
                    prompt="routes messages",
                    a2a={"expose": True, "security": ["apiKey"]},
                )
            ),
            build_agent(AgentSpec(name="billing", engine="echo", streaming=True)),
        ]
    )
    auth = ApiKeyAuthProvider(EnvApiKeyStore(raw=_KEYS))
    return TestClient(create_app(auth=auth, agents=agents))


def _rpc(id_: str, method: str, text: str) -> dict:
    """A JSON-RPC message request carrying a single user-text part."""
    return {
        "jsonrpc": "2.0",
        "id": id_,
        "method": method,
        "params": {"message": {"role": "user", "parts": [{"kind": "text", "text": text}]}},
    }


def test_exposed_agent_serves_schema_valid_card() -> None:
    """An exposed agent's card is public and reflects its real capabilities + declared schemes."""
    client = _client()
    resp = client.get("/a2a/triage/.well-known/agent-card.json")
    assert resp.status_code == 200
    card = resp.json()
    assert card["name"] == "triage"
    assert card["capabilities"]["streaming"] is True
    assert "apiKey" in card["securitySchemes"]
    assert card["url"].endswith("/a2a/triage")


def test_non_exposed_agent_has_no_card() -> None:
    """A Layer 0 agent (no ``a2a`` block) is absent from the network → 404."""
    client = _client()
    assert client.get("/a2a/billing/.well-known/agent-card.json").status_code == 404


def test_agents_index_lists_only_exposed_agents() -> None:
    """The ``/.well-known/agents.json`` index carries exposed agents only."""
    client = _client()
    resp = client.get("/.well-known/agents.json")
    assert resp.status_code == 200
    names = {entry["name"] for entry in resp.json()["agents"]}
    assert names == {"triage"}


def test_message_send_runs_the_agent() -> None:
    """``message/send`` runs the agent and returns an A2A agent message with the output."""
    client = _client()
    resp = client.post(
        "/a2a/triage",
        headers={"x-api-key": "full"},
        json=_rpc("1", "message/send", "hello"),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "1"
    assert body["error"] is None
    result = body["result"]
    assert result["role"] == "agent"
    assert result["parts"][0]["text"] == "echo: hello"


def test_message_send_requires_a2a_scope() -> None:
    """Direct-invoke scope does not grant A2A invoke — the distinct verb is enforced → 403."""
    client = _client()
    resp = client.post(
        "/a2a/triage",
        headers={"x-api-key": "narrow"},
        json=_rpc("1", "message/send", "hello"),
    )
    assert resp.status_code == 403


def test_message_send_needs_a_credential() -> None:
    """The A2A endpoint inherits P04 auth: no credential → 401."""
    client = _client()
    resp = client.post("/a2a/triage", json=_rpc("1", "message/send", "hi"))
    assert resp.status_code == 401


def test_unknown_method_is_json_rpc_error() -> None:
    """An unsupported method returns a JSON-RPC error envelope (code -32601), not a crash."""
    client = _client()
    resp = client.post(
        "/a2a/triage",
        headers={"x-api-key": "full"},
        json=_rpc("9", "tasks/frobnicate", "x"),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["result"] is None
    assert body["error"]["code"] == -32601


def test_message_stream_emits_a2a_status_updates() -> None:
    """``message/stream`` streams SSE JSON-RPC frames ending in a completed status update."""
    client = _client()
    with client.stream(
        "POST",
        "/a2a/triage",
        headers={"x-api-key": "full"},
        json=_rpc("7", "message/stream", "hi"),
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        frames = [
            json.loads(line[len("data:") :].strip())
            for line in resp.iter_lines()
            if (line if isinstance(line, str) else line.decode()).startswith("data:")
        ]
    # Every frame is a JSON-RPC response echoing the request id; the last is terminal + completed.
    assert all(f["id"] == "7" for f in frames)
    assert frames[-1]["result"]["final"] is True
    assert frames[-1]["result"]["status"]["state"] == "completed"


def test_stream_on_nonexposed_agent_is_404() -> None:
    """A non-exposed agent has no A2A endpoint at all."""
    client = _client()
    resp = client.post(
        "/a2a/billing",
        headers={"x-api-key": "full"},
        json=_rpc("1", "message/send", "hi"),
    )
    assert resp.status_code == 404
