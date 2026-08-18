"""The v1 agent surface: invoke, SSE stream, discovery, scope enforcement, and body guard.

These drive the assembled app so they prove the whole path: authenticate → authorize the
scope → resolve the agent → run it as the caller → shape the response. The echo engine
gives a deterministic output (``echo: <input>``) and a real token stream to assert on.
"""

from __future__ import annotations

import json

from agentship.auth import ApiKeyAuthProvider, EnvApiKeyStore
from agentship.engines.base import EngineCapabilities
from agentship.runtime import RunnableAgent, build_agent
from agentship.spec import AgentSpec
from agentship_service import AgentRegistry, create_app
from fastapi.testclient import TestClient


class _NonStreamingEngine:
    """A stub engine that declares it cannot stream (echo always can, so we need our own)."""

    name = "nostream"
    capabilities = EngineCapabilities(streaming=False)

#: Two keys in two tenants: ``full`` holds ``*`` (all agents); ``narrow`` holds only
#: ``agent:support:invoke`` so we can prove a scope that does not cover ``billing``.
_KEYS = json.dumps(
    [
        {"key": "full", "user": "u1", "tenant": "acme", "scopes": ["*"]},
        {"key": "narrow", "user": "u2", "tenant": "beta", "scopes": ["agent:support:invoke"]},
    ]
)


def _client() -> TestClient:
    """Build a client over an app serving ``support`` (streaming) and ``billing`` agents."""
    agents = AgentRegistry(
        [
            build_agent(AgentSpec(name="support", engine="echo", streaming=True)),
            build_agent(AgentSpec(name="billing", engine="echo", streaming=True)),
        ]
    )
    auth = ApiKeyAuthProvider(EnvApiKeyStore(raw=_KEYS))
    return TestClient(create_app(auth=auth, agents=agents))


def test_invoke_runs_agent_as_caller() -> None:
    """A scoped caller invokes an agent and gets the echo output plus the session id back."""
    client = _client()
    resp = client.post(
        "/v1/agents/support:invoke",
        headers={"x-api-key": "full"},
        json={"input": "hello"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["agent"] == "support"
    assert body["output"] == "echo: hello"
    assert body["session_id"]
    assert body["trace_id"] == resp.headers["x-trace-id"]


def test_invoke_echoes_supplied_session_id() -> None:
    """A client-supplied session id is threaded and echoed, not replaced."""
    client = _client()
    resp = client.post(
        "/v1/agents/support:invoke",
        headers={"x-api-key": "full"},
        json={"input": "hi", "session_id": "sess-1"},
    )
    assert resp.json()["session_id"] == "sess-1"


def test_invoke_requires_credential() -> None:
    """No credential → 401 before the agent runs."""
    client = _client()
    resp = client.post("/v1/agents/support:invoke", json={"input": "hi"})
    assert resp.status_code == 401


def test_invoke_forbidden_without_scope() -> None:
    """A caller scoped only to ``support`` may not invoke ``billing`` → 403 forbidden."""
    client = _client()
    resp = client.post(
        "/v1/agents/billing:invoke",
        headers={"x-api-key": "narrow"},
        json={"input": "hi"},
    )
    assert resp.status_code == 403
    assert resp.json()["code"] == "forbidden"


def test_invoke_unknown_agent_is_404_for_authorized_caller() -> None:
    """An authorized caller invoking a missing agent gets 404 (they may learn it is absent)."""
    client = _client()
    resp = client.post(
        "/v1/agents/ghost:invoke",
        headers={"x-api-key": "full"},
        json={"input": "hi"},
    )
    assert resp.status_code == 404
    assert resp.json()["code"] == "not_found"


def test_invoke_rejects_unknown_body_field() -> None:
    """The invoke body forbids extra fields (e.g. a smuggled ``user_id``) → 422."""
    client = _client()
    resp = client.post(
        "/v1/agents/support:invoke",
        headers={"x-api-key": "full"},
        json={"input": "hi", "user_id": "someone-else"},
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "invalid_request"


def test_invoke_body_too_large_is_413() -> None:
    """A body over the size limit is rejected with 413 before it is parsed."""
    client = _client()
    resp = client.post(
        "/v1/agents/support:invoke",
        headers={"x-api-key": "full", "content-length": str(2_000_000)},
        json={"input": "hi"},
    )
    assert resp.status_code == 413
    assert resp.json()["code"] == "payload_too_large"


def test_stream_emits_session_then_events() -> None:
    """The SSE stream opens with a session frame and carries the echo content then done."""
    client = _client()
    with client.stream(
        "POST",
        "/v1/agents/support:stream",
        headers={"x-api-key": "full"},
        json={"input": "hi"},
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        events = _parse_sse(resp.iter_lines())

    assert events[0]["type"] == "session"
    assert events[0]["seq"] == 0
    assert events[0]["data"]["agent"] == "support"
    # Sequence numbers are monotonic and the terminal frame is a done.
    assert [e["seq"] for e in events] == list(range(len(events)))
    assert events[-1]["type"] == "done"
    assert any(e["type"] == "content" for e in events)


def test_stream_rejects_nonstreaming_agent_with_400() -> None:
    """An agent whose engine cannot stream is refused up front (no empty stream)."""
    # Echo always streams, so stand up an agent over a stub engine that declares it cannot.
    quiet = RunnableAgent(
        AgentSpec(name="quiet", engine="echo"), _NonStreamingEngine(), compiled=None
    )
    auth = ApiKeyAuthProvider(EnvApiKeyStore(raw=_KEYS))
    client = TestClient(create_app(auth=auth, agents=AgentRegistry([quiet])))
    resp = client.post(
        "/v1/agents/quiet:stream",
        headers={"x-api-key": "full"},
        json={"input": "hi"},
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "unsupported"


def test_discovery_lists_and_describes_agents() -> None:
    """Any authenticated caller can list the catalog and fetch one agent card."""
    client = _client()
    listed = client.get("/v1/agents", headers={"x-api-key": "narrow"})
    assert listed.status_code == 200
    names = {card["name"] for card in listed.json()}
    assert names == {"support", "billing"}

    one = client.get("/v1/agents/support", headers={"x-api-key": "narrow"})
    assert one.status_code == 200
    assert one.json()["name"] == "support"
    assert one.json()["streaming"] is True


def test_discovery_unknown_agent_is_404() -> None:
    """Fetching a card for a missing agent → 404."""
    client = _client()
    resp = client.get("/v1/agents/ghost", headers={"x-api-key": "full"})
    assert resp.status_code == 404


def _parse_sse(lines) -> list[dict]:
    """Collect ``data:`` payloads from an SSE line stream into parsed event dicts."""
    events = []
    for line in lines:
        text = line.decode() if isinstance(line, bytes) else line
        if text.startswith("data:"):
            events.append(json.loads(text[len("data:") :].strip()))
    return events
