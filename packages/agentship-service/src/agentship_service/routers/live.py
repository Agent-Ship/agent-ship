"""The bidirectional ``/live`` WebSocket: stream turns and interrupt them (barge-in).

A client opens one socket and sends turn messages (``{"input": ..., "session_id"?: ...}``);
the server streams that turn back as typed :class:`StreamEvent` frames. If the client sends
a new turn while one is still streaming, the in-flight turn is cancelled and the new one
starts — this is barge-in, the property that makes a voice/chat UI feel live.

Authentication happens in the middleware before the route (an unauthenticated handshake is
already closed 4401). Here we authorize the caller's scope on the agent and enforce the
close-code contract:

* ``4403`` — authenticated but not scoped to invoke this agent (or no such agent, so its
  existence is not revealed on the socket);
* ``1011`` — an unexpected server error while streaming;
* ``1000`` — normal closure when the client disconnects.
"""

from __future__ import annotations

import asyncio
import uuid

from agentship.auth import authorize
from agentship.errors import AuthError
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..context import current_caller
from ..models.v1 import StreamEvent
from ._common import frame_data, frame_type

router = APIRouter(tags=["agents"])

#: App-defined WebSocket close code (4000–4999 are reserved for the application): the caller
#: is authenticated but not scoped to invoke this agent (or the agent does not exist).
_CLOSE_FORBIDDEN = 4403


@router.websocket("/v1/agents/{name}/live")
async def live(websocket: WebSocket, name: str) -> None:
    """Stream turns of agent ``name`` over one socket, cancelling a turn when a new one arrives."""
    caller = current_caller()
    try:
        authorize(caller, agent=name, verb="invoke")
    except AuthError:
        await websocket.close(code=_CLOSE_FORBIDDEN)
        return
    try:
        agent = websocket.app.state.agents.get(name)
    except KeyError:
        # Do not reveal a missing agent's absence differently from a forbidden one.
        await websocket.close(code=_CLOSE_FORBIDDEN)
        return

    await websocket.accept()
    turn: asyncio.Task | None = None
    try:
        while True:
            message = await websocket.receive_json()
            # Barge-in: a new turn cancels the one still streaming.
            await _cancel(turn)
            turn = asyncio.create_task(_run_turn(websocket, agent, caller, message))
    except WebSocketDisconnect:
        await _cancel(turn)


async def _run_turn(websocket: WebSocket, agent, caller, message: dict) -> None:
    """Stream one turn's frames to the socket; a failure becomes a terminal error frame."""
    text = message.get("input", "")
    session_id = message.get("session_id") or uuid.uuid4().hex
    seq = 0
    await websocket.send_json(
        StreamEvent(
            type="session", seq=seq, data={"session_id": session_id, "agent": agent.spec.name}
        ).model_dump()
    )
    seq += 1
    try:
        async for event in agent.stream(text, caller=caller, session_id=session_id):
            frame = StreamEvent(type=frame_type(event.type), seq=seq, data=frame_data(event))
            await websocket.send_json(frame.model_dump())
            seq += 1
    except asyncio.CancelledError:
        # Barge-in cancelled this turn — expected, let it unwind quietly.
        raise
    except Exception as exc:  # noqa: BLE001 — surface a stream failure as an error frame
        await websocket.send_json(
            StreamEvent(type="error", seq=seq, data={"detail": str(exc)}).model_dump()
        )


async def _cancel(turn: asyncio.Task | None) -> None:
    """Cancel an in-flight turn task and wait for it to unwind, ignoring the cancellation."""
    if turn is not None and not turn.done():
        turn.cancel()
        try:
            await turn
        except asyncio.CancelledError:
            pass
