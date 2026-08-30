"""Offline proof of the chat UI's plumbing — the machinery ``demos/chat_ui.py`` owns.

The UI is a thin browser shell over one async handler, ``respond(message, history, agent,
live_tokens, state)``, which talks to a running AgentShip service over HTTP. These tests drive
that handler and its helpers against a **stubbed transport** — no browser, no server, no key — to
pin what the UI is responsible for:

* the agent picker comes from ``GET /v1/agents``, and an unreachable service is *said out loud*
  rather than silently replaced by a hardcoded list or an in-process run;
* one ``session_id`` is kept across turns (so durable agents remember) and reminted on switch;
* SSE ``:stream`` frames are rendered as they arrive, with their types and ``seq`` in the trace;
* a paused turn's ``resume_token`` is held and the next reply goes to ``:resume`` on the same
  session, with yes/no mapped to ``{"approved": bool}`` and other text passed through;
* a ``problem+json`` error is shown as its ``detail`` and ``code``, not as a stack trace.

The stub is an ``httpx.MockTransport`` installed over ``httpx.Client``/``AsyncClient``, so the
real request-building, header, and SSE-decoding code paths run — only the socket is fake.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# The demo repo ships YAML + tests (not an installed package), so put its root on the path for the
# ``demos`` import (pytest only adds ``tests/`` by default).
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

pytest.importorskip("gradio", reason="the chat UI needs gradio (a dev dependency)")

import httpx  # noqa: E402

import demos.chat_ui as chat_ui  # noqa: E402

#: Two agent cards, the shape ``GET /v1/agents`` returns.
CARDS = [
    {"name": "assistant", "description": "a plain agent", "streaming": True, "capabilities": {}},
    {"name": "note-taker", "description": "pauses first", "streaming": False, "capabilities": {}},
]


def sse_body(frames: list[dict]) -> str:
    """Encode ``StreamEvent`` dicts the way sse-starlette frames them on the wire."""
    return "".join(
        f"event: {frame['type']}\r\ndata: {json.dumps(frame)}\r\n\r\n" for frame in frames
    )


@pytest.fixture
def service(monkeypatch):
    """Install a fake AgentShip service over httpx and record every request the UI makes.

    Routes are registered per test by assigning to ``service.routes`` (keyed by the request path
    plus, for POSTs, nothing else — the demo only ever hits one endpoint per path). The default
    catalog route keeps the common case a one-liner.
    """

    class FakeService:
        """A tiny recorder + router standing in for the real ``/v1`` surface."""

        def __init__(self) -> None:
            """Start with the catalog route wired and an empty request log."""
            self.requests: list[httpx.Request] = []
            self.routes = {"/v1/agents": httpx.Response(200, json=CARDS)}

        def handle(self, request: httpx.Request) -> httpx.Response:
            """Log the request and return the registered response (404 if none is registered)."""
            self.requests.append(request)
            return self.routes.get(
                request.url.path, httpx.Response(404, json={"title": "Not Found", "status": 404})
            )

        def body(self, index: int) -> dict:
            """The decoded JSON body of the n-th recorded request."""
            return json.loads(self.requests[index].content)

    fake = FakeService()
    transport = httpx.MockTransport(fake.handle)
    original_client, original_async = httpx.Client, httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "Client", lambda **kw: original_client(**{**kw, "transport": transport})
    )
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: original_async(**{**kw, "transport": transport})
    )
    monkeypatch.setenv("AGENTSHIP_BASE_URL", "http://service.test")
    monkeypatch.setenv("AGENTSHIP_API_KEY", "test-key")
    return fake


async def drain(handler) -> tuple:
    """Run ``respond``'s async generator to completion and return its last yielded tuple."""
    last = None
    async for value in handler:
        last = value
    return last


def test_the_picker_is_the_services_catalog_and_carries_the_api_key(service):
    """The dropdown is ``GET /v1/agents`` — the service's own list — sent with the bearer key."""
    names, error = chat_ui.fetch_agent_names()

    assert names == ["assistant", "note-taker"]
    assert error is None
    assert service.requests[0].url.path == "/v1/agents"
    assert service.requests[0].headers["authorization"] == "Bearer test-key"


def test_an_unreachable_service_is_said_out_loud_not_papered_over(monkeypatch):
    """A dead service yields no agents and a message naming the URL — never a fallback list.

    This is the whole point of the rewrite: the UI must not quietly run agents in-process when
    the deployment it claims to demonstrate is not there.
    """

    def refuse(request: httpx.Request) -> httpx.Response:
        """Every call fails to connect, as it would with nothing listening on the port."""
        raise httpx.ConnectError("connection refused", request=request)

    original = httpx.Client
    transport = httpx.MockTransport(refuse)
    monkeypatch.setattr(httpx, "Client", lambda **kw: original(**{**kw, "transport": transport}))
    monkeypatch.setenv("AGENTSHIP_BASE_URL", "http://down.test")

    names, error = chat_ui.fetch_agent_names()

    assert names == []
    assert "cannot reach the service at http://down.test" in error.lower()
    assert "make docker-up" in error


def test_a_401_is_shown_as_its_problem_json_detail_and_code(service):
    """An auth failure renders the service's ``detail`` and ``code`` — its real behaviour."""
    service.routes["/v1/agents"] = httpx.Response(
        401,
        json={
            "type": "about:blank",
            "title": "Unauthorized",
            "status": 401,
            "detail": "no API key — send it in the 'x-api-key' header",
            "code": "no_credentials",
            "trace_id": "abc123",
        },
    )

    names, error = chat_ui.fetch_agent_names()

    assert names == []
    assert "Unauthorized" in error
    assert "no API key" in error
    assert "code: no_credentials" in error
    assert "Traceback" not in error


def test_resume_value_maps_yes_no_and_passes_other_text_through():
    """A yes/no reply to a pause becomes ``{"approved": bool}``; other text passes through."""
    assert chat_ui.resume_value_from_reply("yes") == {"approved": True}
    assert chat_ui.resume_value_from_reply("no") == {"approved": False}
    assert chat_ui.resume_value_from_reply("use 3 sources") == "use 3 sources"


def test_a_pause_is_only_a_token_with_no_output():
    """A durable turn that *finished* also returns a token, so the token alone is not a pause."""
    assert chat_ui.is_paused({"output": None, "resume_token": {"engine": "langgraph"}})
    assert not chat_ui.is_paused({"output": "done", "resume_token": {"engine": "langgraph"}})
    assert not chat_ui.is_paused({"output": None, "resume_token": None})


async def test_a_streamed_turn_renders_frames_and_traces_their_types_and_seq(service):
    """``:stream`` tokens land in the transcript as they arrive and the trace names each frame."""
    service.routes["/v1/agents/assistant:stream"] = httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        text=sse_body(
            [
                {"type": "session", "seq": 0, "data": {"session_id": "s1", "agent": "assistant"}},
                {"type": "content", "seq": 1, "data": {"content": "Hello"}},
                {"type": "content", "seq": 2, "data": {"content": ", world"}},
                {"type": "done", "seq": 3, "data": {}},
            ]
        ),
    )
    state = chat_ui.new_state()

    history, box, state, trace = await drain(
        chat_ui.respond("hi", [], "assistant", True, state)
    )

    assert box == ""
    assert history[-1]["content"] == "Hello, world"
    assert "/v1/agents/assistant:stream" in trace
    # The panel shows what the wire carried: every frame's type against its sequence number.
    for expected in ("seq   0  session", "seq   1  content", "seq   3  done"):
        assert expected in trace


async def test_one_session_id_is_kept_across_turns_and_reminted_on_agent_switch(service):
    """Durable memory depends on the thread: same agent keeps the session, a new agent starts one."""
    service.routes["/v1/agents/assistant:invoke"] = httpx.Response(
        200, json={"agent": "assistant", "session_id": "s1", "output": "hi", "resume_token": None}
    )
    service.routes["/v1/agents/note-taker:invoke"] = httpx.Response(
        200, json={"agent": "note-taker", "session_id": "s2", "output": "ok", "resume_token": None}
    )
    state = chat_ui.new_state()

    history, _, state, _ = await drain(chat_ui.respond("hi", [], "assistant", False, state))
    first = state["session_id"]
    assert first, "a conversation thread should be opened on the first turn"
    assert history[-1]["content"] == "hi"

    history, _, state, _ = await drain(chat_ui.respond("again", history, "assistant", False, state))
    assert state["session_id"] == first
    assert service.body(0)["session_id"] == first
    assert service.body(1)["session_id"] == first

    # Switching agent is a different conversation, so it must not reuse the old thread.
    _, _, state, _ = await drain(chat_ui.respond("hello", history, "note-taker", False, state))
    assert state["session_id"] != first


async def test_a_paused_turn_is_held_then_resumed_on_the_same_session(service):
    """A pause is shown, the token held, and the next reply goes to ``:resume`` with that token."""
    token = {"engine": "langgraph", "blob": {"thread_id": "t1", "interrupt": True}}
    service.routes["/v1/agents/note-taker:invoke"] = httpx.Response(
        200,
        json={"agent": "note-taker", "session_id": "s1", "output": None, "resume_token": token},
    )
    service.routes["/v1/agents/note-taker:resume"] = httpx.Response(
        200,
        json={
            "agent": "note-taker",
            "session_id": "s1",
            "output": "saved: buy milk",
            "resume_token": None,
        },
    )
    state = chat_ui.new_state()

    history, _, state, _ = await drain(
        chat_ui.respond("save a note: buy milk", [], "note-taker", False, state)
    )
    assert state["resume_token"] == token
    assert "paused" in history[-1]["content"].lower()
    session_id = state["session_id"]

    history, _, state, trace = await drain(
        chat_ui.respond("yes", history, "note-taker", False, state)
    )
    resume_body = service.body(1)
    assert service.requests[1].url.path == "/v1/agents/note-taker:resume"
    assert resume_body["resume_token"] == token
    assert resume_body["session_id"] == session_id, "a resume must replay the paused thread"
    assert resume_body["resume_value"] == {"approved": True}
    assert state["resume_token"] is None
    assert history[-1]["content"] == "saved: buy milk"
    assert ":resume" in trace


async def test_a_failed_turn_is_shown_in_the_chat_not_raised(service):
    """A 403 during a turn renders in the transcript and clears any stale pause, without crashing."""
    service.routes["/v1/agents/assistant:invoke"] = httpx.Response(
        403,
        json={
            "title": "Forbidden",
            "status": 403,
            "detail": "missing scope agent:assistant:invoke",
            "code": "forbidden",
        },
    )
    state = chat_ui.new_state()

    history, box, state, _ = await drain(chat_ui.respond("hi", [], "assistant", False, state))

    assert box == "" and state["resume_token"] is None
    assert "Forbidden" in history[-1]["content"]
    assert "missing scope" in history[-1]["content"]
    assert "code: forbidden" in history[-1]["content"]
