"""BFF (backend-for-frontend) sample: hold the API key server-side, stream to the browser.

This is the **recommended** pattern for browser apps. The browser never holds an AgentShip
credential; it talks to this same-origin BFF, which authenticates the user however it likes
(a session cookie here, stubbed) and then calls AgentShip with the service API key, relaying
the SSE stream straight back to the page.

Run the AgentShip service first (``agentship serve --agents-dir examples`` or
``make docker-up``), then::

    AGENTSHIP_BASE_URL=http://localhost:8000 AGENTSHIP_API_KEY=dev \\
        uvicorn examples.frontend.bff.app:app --port 3000

Open http://localhost:3000/ and send a message — the key stays on the server.
"""

from __future__ import annotations

import os

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse

app = FastAPI(title="AgentShip BFF sample")

#: Where the AgentShip service lives and the service key the BFF calls it with. The key is
#: read from the environment and never sent to the browser.
BASE_URL = os.environ.get("AGENTSHIP_BASE_URL", "http://localhost:8000")
API_KEY = os.environ.get("AGENTSHIP_API_KEY", "dev")
AGENT = os.environ.get("AGENTSHIP_AGENT", "hello")


def current_user(request: Request) -> str:
    """Authenticate the browser user (stub).

    A real BFF checks its own session cookie / OIDC here and derives the user. This sample
    returns a fixed user so the flow is runnable; the point is that the *browser* holds a
    BFF session, not an AgentShip key.
    """
    return request.cookies.get("bff_user", "demo-user")


@app.post("/chat")
async def chat(request: Request) -> StreamingResponse:
    """Proxy a chat turn to AgentShip's ``:stream`` endpoint, relaying SSE to the browser.

    The browser POSTs ``{"input": ...}`` to this same-origin route; the BFF adds the service
    API key and streams the ``text/event-stream`` response back unchanged.
    """
    _ = current_user(request)  # gate on the BFF's own session in a real app
    body = await request.json()

    async def relay():
        """Open the upstream stream and yield its bytes as they arrive."""
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream(
                "POST",
                f"{BASE_URL}/v1/agents/{AGENT}:stream",
                headers={"x-api-key": API_KEY, "accept": "text/event-stream"},
                json={"input": body.get("input", "")},
            ) as upstream:
                async for chunk in upstream.aiter_bytes():
                    yield chunk

    return StreamingResponse(relay(), media_type="text/event-stream")


@app.get("/")
async def index() -> HTMLResponse:
    """A minimal page that streams from the same-origin ``/chat`` (no key in the browser)."""
    return HTMLResponse(_PAGE)


#: The browser page: an EventSource-style fetch reader against the same-origin BFF. It never
#: sees an AgentShip API key — that lives only on the server.
_PAGE = """<!doctype html>
<meta charset="utf-8">
<title>AgentShip BFF sample</title>
<h1>AgentShip BFF sample</h1>
<input id="msg" value="hello there" size="40">
<button onclick="send()">Send</button>
<pre id="out"></pre>
<script>
async function send() {
  const out = document.getElementById('out');
  out.textContent = '';
  const resp = await fetch('/chat', {
    method: 'POST',
    headers: {'content-type': 'application/json'},
    body: JSON.stringify({input: document.getElementById('msg').value}),
  });
  const reader = resp.body.getReader();
  const dec = new TextDecoder();
  for (;;) {
    const {value, done} = await reader.read();
    if (done) break;
    out.textContent += dec.decode(value);
  }
}
</script>
"""
