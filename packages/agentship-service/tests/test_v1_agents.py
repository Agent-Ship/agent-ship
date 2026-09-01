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


# ---- :resume — the endpoint that makes a returned resume_token usable ---------------------------


def test_resume_requires_the_invoke_scope() -> None:
    """``:resume`` continues a run, so it is gated by the same scope as ``:invoke``."""
    client = _client()
    body = {"resume_token": {"engine": "echo", "blob": {}}, "session_id": "s1"}
    assert (
        client.post(
            "/v1/agents/billing:resume", json=body, headers={"X-API-Key": "narrow"}
        ).status_code
        == 403
    )


def test_resume_on_a_non_durable_engine_is_a_clean_error() -> None:
    """Resuming an engine that declares ``durability="none"`` fails loudly, never silently.

    The echo engine is not durable, so this must surface as a real error response rather
    than pretending the run continued. Declare-don't-fake, enforced at the HTTP edge.
    """
    client = _client()
    body = {"resume_token": {"engine": "echo", "blob": {}}, "session_id": "s1"}
    response = client.post("/v1/agents/support:resume", json=body, headers={"X-API-Key": "full"})
    # 400: asking a non-durable engine to resume is a bad request, not a server fault.
    assert response.status_code == 400
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "unsupported"


def test_resume_rejects_a_body_without_a_token() -> None:
    """A resume with no token is a malformed request, not an empty resume."""
    client = _client()
    response = client.post(
        "/v1/agents/support:resume", json={"session_id": "s1"}, headers={"X-API-Key": "full"}
    )
    assert response.status_code == 422


def test_a_finished_durable_run_is_not_reported_as_paused() -> None:
    """A durable run that ANSWERED carries a resume token but must not look paused.

    The token is the crash-resume handle — every durable run gets one, finished or not. A
    client that treats "token present" as "paused" tells the user their answered turn is
    waiting for approval, which is what Studio did: saying "hi" to a durable agent replied
    normally AND claimed the run was paused.
    """
    client = _client()
    body = {"input": "hi", "session_id": "s-done"}
    reply = client.post(
        "/v1/agents/support:invoke", json=body, headers={"X-API-Key": "full"}
    ).json()

    assert reply["output"], "the run answered"
    assert reply["paused"] is False, "an answered run is not waiting for a human"


class _PausingEngine:
    """An engine whose run pauses for a human AND emits partial output.

    That combination is the case the old `paused` rule got wrong: it inferred a pause from
    "no output", so any pause that had already said something was reported as finished.
    """

    name = "pausing"
    capabilities = EngineCapabilities(durability="checkpoint")

    def build(self, spec, authored=None):
        """Nothing to compile; the tests drive run/resume directly."""
        return object()

    async def run(self, compiled, text, ctx):
        """Pause for approval, having already produced some visible output."""
        from agentship.engines.base import Result, ResumeToken

        return Result(
            output="I drafted the email. Send it?",
            resume_token=ResumeToken(engine=self.name, blob={}),
            interrupt={"action": "confirm_write", "tool": "send_email"},
        )


def _pausing_client() -> TestClient:
    """A client over an app serving one agent on the pausing engine."""
    from agentship.engines.base import ENGINES

    ENGINES.register("pausing", _PausingEngine)
    agents = AgentRegistry([build_agent(AgentSpec(name="drafter", engine="pausing"))])
    auth = ApiKeyAuthProvider(EnvApiKeyStore(raw=_KEYS))
    return TestClient(create_app(auth=auth, agents=agents))


def test_a_pause_that_already_said_something_is_still_a_pause() -> None:
    """A run can produce output AND still be waiting on a human.

    The rule was `resume_token is not None and not result.output`, so any pause that had
    emitted text reported paused=false — and a client would discard a live resume token,
    leaving the run unresumable. The engine already reports the pause on Result.interrupt;
    inferring it from emptiness was the mistake.
    """
    try:
        reply = _pausing_client().post(
            "/v1/agents/drafter:invoke",
            json={"input": "draft an email"},
            headers={"X-API-Key": "full"},
        ).json()
    finally:
        from agentship.engines.base import ENGINES

        ENGINES._providers.pop("pausing", None)

    assert reply["output"], "this run did produce output"
    assert reply["paused"] is True, "a run waiting on a human is paused, output or not"


def test_a_pause_tells_the_client_what_it_is_asking() -> None:
    """The interrupt payload reaches the client, so it can render the actual question.

    Without it a UI can only say "this run paused" and cannot show WHAT is being approved.
    """
    try:
        reply = _pausing_client().post(
            "/v1/agents/drafter:invoke",
            json={"input": "draft an email"},
            headers={"X-API-Key": "full"},
        ).json()
    finally:
        from agentship.engines.base import ENGINES

        ENGINES._providers.pop("pausing", None)

    assert reply["interrupt"] == {"action": "confirm_write", "tool": "send_email"}


def test_the_agent_card_reports_the_AGENT_not_the_engine() -> None:
    """A card must describe what THIS agent does, not what its engine could do.

    The card was built from ``agent.engine.capabilities``, so every LangGraph agent
    advertised ``durability: checkpoint`` — including the ones declaring
    ``durability: none``. Studio renders that as a badge, so 7 of 8 demo agents claimed
    to remember conversations when they keep no state at all, and a user reasonably
    concluded memory was broken.
    """
    agents = AgentRegistry(
        [
            # langgraph, because it is the engine that CAN checkpoint — which is exactly
            # why it advertised checkpointing for agents that had not asked for it.
            build_agent(AgentSpec(name="forgetful", engine="langgraph", model="x")),
            build_agent(
                AgentSpec(
                    name="remembers", engine="langgraph", model="x", durability="checkpoint"
                )
            ),
        ]
    )
    auth = ApiKeyAuthProvider(EnvApiKeyStore(raw=_KEYS))
    cards = TestClient(create_app(auth=auth, agents=agents)).get(
        "/v1/agents", headers={"X-API-Key": "full"}
    ).json()
    by_name = {c["name"]: c for c in cards}

    assert by_name["forgetful"]["capabilities"]["durability"] == "none", (
        "an agent that declares durability: none must not advertise checkpointing"
    )
    assert by_name["remembers"]["capabilities"]["durability"] == "checkpoint"
