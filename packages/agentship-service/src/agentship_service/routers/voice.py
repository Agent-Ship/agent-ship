"""The ``/voice`` WebSocket: talk to an agent over the service you already run.

Voice is a capability of this service, not a second product on another port. A client opens
one socket, sends microphone audio as raw PCM, and hears the agent answer — through the same
authentication, the same agent registry and the same tenant scoping as ``:invoke``. Studio
uses it; so can anything else that can open a socket.

The pipeline itself is not built here. ``agentship-voice`` owns the cascade and both framework
adapters; this route only hands it a transport and the caller's identity. That keeps the
service free of any voice framework: the import below is lazy, and a deployment without the
voice package answers with a close code instead of failing to start.

Close codes match ``/live`` so a client handles one contract:

* ``4403`` — authenticated but not scoped to invoke this agent (or no such agent);
* ``4404`` — this agent has no ``voice:`` block, so there is nothing to talk to;
* ``4503`` — the server cannot do voice: the package or a provider key is missing;
* ``1011`` — an unexpected server error;
* ``1000`` — normal closure.
"""

from __future__ import annotations

import logging

from agentship.auth import authorize
from agentship.errors import AuthError
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..context import current_caller

router = APIRouter(tags=["agents"])

logger = logging.getLogger("agentship.service")

#: See the module docstring — one close-code contract shared with ``/live``.
_CLOSE_FORBIDDEN = 4403
_CLOSE_NOT_VOICE = 4404
_CLOSE_UNAVAILABLE = 4503

#: The rate the whole socket runs at, capture and playback.
#:
#: 24 kHz because that is what OpenAI's speech synthesis produces and it refuses to resample.
#: Running the socket at 16 kHz meant 24 kHz audio was played back as though it were 16 kHz —
#: the same samples stretched over 1.5x the time, which sounds like the agent talking slowly in
#: a deeper voice. Nothing errored; it just sounded wrong, which is the hardest kind of bug to
#: attribute. Recognition is happy at either rate, so the synthesiser's requirement wins.
SAMPLE_RATE = 24000


@router.websocket("/v1/agents/{name}/voice")
async def voice(websocket: WebSocket, name: str) -> None:
    """Run one spoken conversation with agent ``name`` over this socket."""
    # Accept FIRST, then refuse with a code if we must.
    #
    # A close before `accept()` is not a WebSocket close at all — Starlette answers the
    # handshake with a bare ``HTTP 403``, and every code below is discarded. The browser is
    # handed a failed connection carrying no code and no reason, so a client that waits to be
    # told why waits forever: pressing the microphone on an agent with no `voice:` block
    # reported "timed out opening the voice socket" instead of "this agent has no voice block".
    # The whole close-code contract in this module's docstring was unobservable over a real
    # connection, and only looked right because TestClient surfaces the pre-accept close.
    #
    # A browser that offered a subprotocol also drops the connection unless the server echoes
    # one back, so the handshake answers with the same "bearer" the credential arrived on.
    offered = websocket.scope.get("subprotocols", [])
    await websocket.accept(subprotocol="bearer" if "bearer" in offered else None)

    caller = current_caller()
    try:
        authorize(caller, agent=name, verb="invoke")
    except AuthError:
        await websocket.close(code=_CLOSE_FORBIDDEN, reason="not scoped to invoke this agent")
        return
    try:
        agent = websocket.app.state.agents.get(name)
    except KeyError:
        # Do not distinguish a missing agent from a forbidden one on the socket.
        await websocket.close(code=_CLOSE_FORBIDDEN, reason="not scoped to invoke this agent")
        return

    if agent.spec.voice is None:
        await websocket.close(code=_CLOSE_NOT_VOICE, reason="agent has no `voice:` block")
        return

    try:
        run_session = _load_voice()
    except ImportError:
        logger.warning("voice requested but agentship-voice is not installed")
        await websocket.close(code=_CLOSE_UNAVAILABLE, reason="voice package not installed")
        return

    try:
        await run_session(websocket, agent, caller, SAMPLE_RATE)
    except WebSocketDisconnect:
        pass  # the human hung up; not a failure
    except Exception as exc:
        logger.exception("voice session failed for agent=%s", name)
        # Send the reason, not just the code. The most common failure here is an unset provider
        # key, and its message already names the variable — telling the human "voice failed"
        # while the server knows it needs DEEPGRAM_API_KEY wastes the one piece of information
        # that would have fixed it. A close reason is capped at 123 BYTES by the protocol, and
        # a longer one makes the close frame itself invalid, so it is truncated here.
        await websocket.close(code=1011, reason=str(exc).encode()[:120].decode(errors="ignore"))


def _load_voice():
    """Return the session runner from ``agentship-voice``, or raise ``ImportError``.

    Imported on first use so this service neither depends on a voice framework nor fails to
    start without one — the same lazy-resolution rule engines and MCP follow.
    """
    from agentship_voice.session import run_browser_session

    return run_browser_session
