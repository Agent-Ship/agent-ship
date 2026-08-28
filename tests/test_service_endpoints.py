"""Demo slice (Phase 06): the three transports and the error model, over a real server.

This is the surface a user integrates against. A booted AgentShip service answers the
same agent three ways — one JSON response, a Server-Sent-Event stream, and a WebSocket
round-trip — and turns a malformed request into an RFC-9457 problem document rather than
a stack trace or a bare 500.

The server is a real uvicorn process bound to a loopback port, not an in-process
``TestClient``. That difference is the point of this slice: ``TestClient`` calls the ASGI
app directly, so it would stay green even if the app could not boot, bind a socket, or
complete a WebSocket handshake. Everything here goes over a socket.

The served agent runs on the ``echo`` engine (``agents/service/support.yaml``), so the
whole file is keyless and offline — what is under test is the transport and the error
contract, not the model.

    pytest tests/test_service_endpoints.py
"""

from __future__ import annotations

import json

import httpx
import pytest
from conftest import ACME, build_service_app, serve_on_loopback
from websockets.sync.client import connect as connect_websocket

#: The agent the demo service serves; its routes are `/v1/agents/support:...`.
AGENT = "support"


@pytest.fixture(scope="module")
def base_url():
    """Boot the demo service once for this module and yield its ``http://127.0.0.1:PORT``."""
    with serve_on_loopback(build_service_app()) as url:
        yield url


def test_the_server_boots_and_reports_itself_live(base_url):
    """The booted app answers its unauthenticated liveness probe — the socket really serves."""
    response = httpx.get(f"{base_url}/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_invoke_returns_one_json_result(base_url):
    """`POST :invoke` runs one turn and returns the answer as JSON with its session id.

    The session id is minted server-side and echoed back — that is the handle a client
    threads through the next turn to continue the same conversation.
    """
    response = httpx.post(
        f"{base_url}/v1/agents/{AGENT}:invoke", headers=ACME, json={"input": "hello"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["agent"] == AGENT
    assert body["output"] == "echo: hello"
    assert body["session_id"]


def test_stream_delivers_more_than_one_sse_chunk(base_url):
    """`POST :stream` delivers the turn as several ordered SSE frames, not one blob.

    More than one chunk is the assertion that matters: a single frame would prove
    nothing beyond a slow JSON response. The frames open with a ``session`` frame, carry
    the content, and close with a terminal ``done``; ``seq`` is gap-free from 0 so a
    client can detect a dropped frame.
    """
    frames = []
    with httpx.stream(
        "POST",
        f"{base_url}/v1/agents/{AGENT}:stream",
        headers=ACME,
        json={"input": "hello"},
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        for line in response.iter_lines():
            if line.startswith("data:"):
                frames.append(json.loads(line.removeprefix("data:").strip()))

    assert len(frames) > 1, f"streaming produced only {len(frames)} frame(s)"
    assert [frame["seq"] for frame in frames] == list(range(len(frames)))
    assert frames[0]["type"] == "session"
    assert frames[-1]["type"] == "done"
    assert "echo: hello" in "".join(str(frame["data"].get("content", "")) for frame in frames)


def test_websocket_round_trip_streams_a_turn_back(base_url):
    """The `/live` socket accepts a turn message and streams that turn's frames back.

    One socket carries many turns, which is what a chat or voice UI needs; here we send
    one and read until the terminal frame.
    """
    url = f"{base_url.replace('http://', 'ws://')}/v1/agents/{AGENT}/live"
    with connect_websocket(url, additional_headers=ACME) as socket:
        socket.send(json.dumps({"input": "hello"}))
        frames = []
        while not frames or frames[-1]["type"] not in ("done", "error"):
            frames.append(json.loads(socket.recv(timeout=10)))

    assert frames[0]["type"] == "session"
    assert frames[-1]["type"] == "done"
    assert any(frame["data"].get("content") == "echo: hello" for frame in frames)


def test_a_bad_request_renders_as_an_rfc_9457_problem_document(base_url):
    """A malformed body comes back as `application/problem+json`, machine-readable.

    RFC 9457 is what lets a client branch on a failure without scraping prose: the media
    type identifies the body as a problem document, and ``status``/``title``/``detail``
    describe it. AgentShip adds ``code`` (a stable machine identifier) and ``trace_id``
    (the id to quote in a bug report).
    """
    response = httpx.post(
        f"{base_url}/v1/agents/{AGENT}:invoke", headers=ACME, json={"not_the_input_field": 1}
    )

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")
    problem = response.json()
    assert problem["type"] == "about:blank"
    assert problem["title"] == "Unprocessable Entity"
    assert problem["status"] == 422
    assert "body.input" in problem["detail"]
    assert problem["code"] == "invalid_request"
    assert problem["trace_id"]


def test_an_unknown_agent_is_a_problem_document_too(base_url):
    """Asking for an agent that does not exist is a 404 problem document, not an HTML page."""
    response = httpx.post(
        f"{base_url}/v1/agents/nosuchagent:invoke", headers=ACME, json={"input": "hello"}
    )

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "not_found"


def test_discovery_lists_the_served_agent(base_url):
    """`GET /v1/agents` returns a card per served agent, so a client needs no out-of-band docs."""
    response = httpx.get(f"{base_url}/v1/agents", headers=ACME)

    assert response.status_code == 200
    cards = {card["name"]: card for card in response.json()}
    assert AGENT in cards
    assert cards[AGENT]["streaming"] is True
