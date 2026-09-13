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

    async def resume(self, compiled, token, ctx, *, resume_value=None):
        """Finish the paused run, echoing the human's decision so the test can see it arrive.

        Echoing ``resume_value`` is the point: it proves the decision travelled from the
        request body through the router into the engine, rather than the endpoint merely
        returning a plausible-looking 200.
        """
        from agentship.engines.base import Result

        approved = (resume_value or {}).get("approved")
        return Result(output=f"sent, approved={approved}", resume_token=None, interrupt=None)


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
        reply = (
            _pausing_client()
            .post(
                "/v1/agents/drafter:invoke",
                json={"input": "draft an email"},
                headers={"X-API-Key": "full"},
            )
            .json()
        )
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
        reply = (
            _pausing_client()
            .post(
                "/v1/agents/drafter:invoke",
                json={"input": "draft an email"},
                headers={"X-API-Key": "full"},
            )
            .json()
        )
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
                AgentSpec(name="remembers", engine="langgraph", model="x", durability="checkpoint")
            ),
        ]
    )
    auth = ApiKeyAuthProvider(EnvApiKeyStore(raw=_KEYS))
    cards = (
        TestClient(create_app(auth=auth, agents=agents))
        .get("/v1/agents", headers={"X-API-Key": "full"})
        .json()
    )
    by_name = {c["name"]: c for c in cards}

    assert by_name["forgetful"]["capabilities"]["durability"] == "none", (
        "an agent that declares durability: none must not advertise checkpointing"
    )
    assert by_name["remembers"]["capabilities"]["durability"] == "checkpoint"


def test_an_agent_can_stream_even_when_its_yaml_does_not_say_streaming() -> None:
    """``streaming`` on the card is a CAPABILITY, not an opt-in like ``durability``.

    ``spec.streaming`` is a build-time request the capability gate checks; it is not a
    statement that the agent must not stream. Reporting `spec.streaming and caps.streaming`
    made every agent that omitted the field advertise streaming: false, so Studio silently
    fell back to :invoke and token streaming stopped working for 7 of 8 demo agents.
    """
    agents = AgentRegistry([build_agent(AgentSpec(name="quiet", engine="echo"))])
    auth = ApiKeyAuthProvider(EnvApiKeyStore(raw=_KEYS))
    card = (
        TestClient(create_app(auth=auth, agents=agents))
        .get("/v1/agents/quiet", headers={"X-API-Key": "full"})
        .json()
    )

    assert card["streaming"] is True, "an agent on a streaming engine can be streamed"
    assert card["capabilities"]["streaming"] is True


# ---- :resume — the paths a pause is actually completed through ----------------------------------


def test_resume_completes_a_paused_run() -> None:
    """The happy path: a pause is resumed with the human's decision and the run finishes.

    Everything else about `:resume` was tested through its refusals — wrong scope, a
    non-durable engine, a body with no token. None of them proved the endpoint can do the
    one thing it exists for, so a resume that returned the wrong shape, dropped
    `resume_value`, or never reached the engine would have gone unnoticed.
    """
    client = _pausing_client()

    paused = client.post(
        "/v1/agents/drafter:invoke", json={"input": "email bob"}, headers={"x-api-key": "full"}
    ).json()
    assert paused["paused"] is True, "the run should be waiting on a human"

    resumed = client.post(
        "/v1/agents/drafter:resume",
        json={
            "resume_token": paused["resume_token"],
            "session_id": paused["session_id"],
            "resume_value": {"approved": True},
        },
        headers={"x-api-key": "full"},
    )

    assert resumed.status_code == 200
    body = resumed.json()
    assert body["output"] == "sent, approved=True", "the decision must reach the engine"
    assert body["paused"] is False, "a completed resume is not still waiting"
    assert body["resume_token"] is None, "a finished run hands back no token to resume again"
    assert body["session_id"] == paused["session_id"], "a resume stays on the same thread"


def test_a_malformed_resume_token_is_the_callers_fault() -> None:
    """A token that is not a token is a 422, not a 500.

    `resume_token` was typed `dict`, so anything JSON-shaped passed the boundary and was
    validated inside the handler instead. The ValidationError that came back was nobody's
    registered error, so the caller was told their own bad input was a server fault — and,
    because Starlette renders that 500 outside the middleware stack, the response also
    lost every security header and its trace id.
    """
    client = _pausing_client()

    response = client.post(
        "/v1/agents/drafter:resume",
        json={"resume_token": {"nonsense": True}, "session_id": "s1"},
        headers={"x-api-key": "full"},
    )

    assert response.status_code == 422, "a malformed token is a bad request"
    body = response.json()
    assert body["code"] == "invalid_request"
    assert "resume_token" in (body["detail"] or ""), "say which field was wrong"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert body["trace_id"], "the response a caller reports must carry a trace id"


# ---- what a playground needs: how long it took, and what it is talking to -----------------------


def test_a_turn_reports_how_long_it_took() -> None:
    """Latency is part of the answer, not a debug log — you cannot tune what the API hides."""
    client = _client()
    body = client.post(
        "/v1/agents/support:invoke", json={"input": "hi"}, headers={"x-api-key": "full"}
    ).json()

    assert body["timings"]["total_ms"] is not None
    assert body["timings"]["total_ms"] >= 0
    # A non-streaming turn has no first-token moment distinct from its last: the caller waits
    # for the whole reply either way, so reporting a ttft here would be inventing a number.
    assert body["timings"]["ttft_ms"] is None


def test_a_streamed_turn_reports_first_audio_apart_from_total() -> None:
    """``ttft`` is the wait a reader feels; ``total`` is the whole reply. The gap is streaming.

    They arrive on the engine's own terminal frame rather than an extra one — a client that
    stops at the first ``done`` must still get them, and one that does not must not see the
    turn end twice.
    """
    client = _client()
    with client.stream(
        "POST", "/v1/agents/support:stream", headers={"x-api-key": "full"}, json={"input": "hi"}
    ) as resp:
        events = _parse_sse(resp.iter_lines())

    assert sum(1 for e in events if e["type"] == "done") == 1, "exactly one terminal frame"
    timings = events[-1]["data"]["timings"]
    assert timings["ttft_ms"] is not None, "a streamed turn knows when words first arrived"
    assert timings["ttft_ms"] <= timings["total_ms"]


def test_a_card_publishes_the_spec_the_agent_actually_is() -> None:
    """A client can show what it is talking to without a second endpoint or the repository.

    Secrets never live in a spec — they come from the environment — which is what makes the
    declared contract safe to publish here.
    """
    client = _client()
    card = client.get("/v1/agents/support", headers={"x-api-key": "full"}).json()

    assert card["spec"]["name"] == "support"
    assert card["spec"]["engine"] == "echo"
    # exclude_none: a card shows what the author wrote, not every default the model carries.
    assert "model" not in card["spec"], "an unset field is not configuration anybody chose"
