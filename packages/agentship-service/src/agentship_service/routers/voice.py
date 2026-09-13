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

#: The rate Studio captures and plays at. Browsers resample happily; 16 kHz is what speech
#: recognition wants and keeps the socket a third the size of 48 kHz for no audible loss.
SAMPLE_RATE = 16000


@router.websocket("/v1/agents/{name}/voice")
async def voice(websocket: WebSocket, name: str) -> None:
    """Run one spoken conversation with agent ``name`` over this socket."""
    caller = current_caller()
    try:
        authorize(caller, agent=name, verb="invoke")
    except AuthError:
        await websocket.close(code=_CLOSE_FORBIDDEN)
        return
    try:
        agent = websocket.app.state.agents.get(name)
    except KeyError:
        # Do not distinguish a missing agent from a forbidden one on the socket.
        await websocket.close(code=_CLOSE_FORBIDDEN)
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

    # A browser that offered a subprotocol drops the connection unless the server echoes one
    # back, so the handshake has to answer with the same "bearer" the credential arrived on.
    offered = websocket.scope.get("subprotocols", [])
    await websocket.accept(subprotocol="bearer" if "bearer" in offered else None)
    try:
        await run_session(websocket, agent, caller, SAMPLE_RATE)
    except WebSocketDisconnect:
        pass  # the human hung up; not a failure
    except Exception:
        logger.exception("voice session failed for agent=%s", name)
        await websocket.close(code=1011)


def _load_voice():
    """Return the session runner from ``agentship-voice``, or raise ``ImportError``.

    Imported on first use so this service neither depends on a voice framework nor fails to
    start without one — the same lazy-resolution rule engines and MCP follow.
    """
    from agentship_voice.session import run_browser_session

    return run_browser_session
