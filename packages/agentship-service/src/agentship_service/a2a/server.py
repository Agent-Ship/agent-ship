"""Dispatch A2A JSON-RPC methods onto a built agent (§C4 adapter).

The A2A method table maps onto the same ``BaseAgent`` run/stream port the ``/v1`` routes use, so
an agent exposed over A2A behaves identically to one invoked directly — one runtime, two protocols:

* ``message/send``  → :meth:`RunnableAgent.run`   → an A2A agent ``Message`` result.
* ``message/stream`` → :meth:`RunnableAgent.stream` → SSE frames of A2A ``TaskStatusUpdate``
  results, reusing the ``/v1`` SSE event normalisation so both surfaces stream the same events.

Long-running ``tasks/*`` methods are recognised and answered with a not-implemented JSON-RPC error
for now (they land with the P11 task bridge, C5); an unknown method returns the standard JSON-RPC
"method not found" (-32601) rather than crashing.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator

from agentship.a2a.models import JsonRpcRequest, JsonRpcResponse
from agentship.context import Caller
from agentship.runtime import RunnableAgent

from ..routers._common import frame_data, frame_type
from .mapping import message_text, result_message, status_update

#: JSON-RPC "method not found" per the spec, returned for any method we do not serve.
_METHOD_NOT_FOUND = -32601

#: Task lifecycle methods A2A defines but we do not serve yet (they arrive with the P11 bridge).
_DEFERRED_TASK_METHODS = frozenset(
    {"tasks/get", "tasks/cancel", "tasks/resubscribe", "tasks/pushNotificationConfig/set"}
)


async def handle_rpc(agent: RunnableAgent, caller: Caller, req: JsonRpcRequest) -> JsonRpcResponse:
    """Handle a non-streaming A2A method, returning a JSON-RPC response envelope.

    Only ``message/send`` runs the agent today; ``tasks/*`` are recognised-but-deferred and any
    other method is a "method not found" error. A recognised-but-deferred or unknown method is a
    JSON-RPC *error*, never an HTTP error — the transport call succeeded, the method did not.
    """
    if req.method == "message/send":
        text = message_text(req.params)
        result = await agent.run(text, caller=caller, session_id=uuid.uuid4().hex)
        return JsonRpcResponse.ok(req.id, result_message(result.output))
    if req.method in _DEFERRED_TASK_METHODS:
        return JsonRpcResponse.fail(
            req.id, _METHOD_NOT_FOUND, f"method {req.method!r} is not implemented yet (P11 tasks)"
        )
    return JsonRpcResponse.fail(req.id, _METHOD_NOT_FOUND, f"unknown method {req.method!r}")


async def stream_rpc(
    agent: RunnableAgent, caller: Caller, req: JsonRpcRequest
) -> AsyncIterator[dict]:
    """Stream an A2A ``message/stream`` turn as SSE JSON-RPC frames of task status updates.

    Each engine event with text becomes a ``working`` status update carrying that text; the stream
    always ends with a terminal frame — ``completed`` on success, ``failed`` if the engine raised
    mid-stream (the HTTP status was already 200 once streaming began, so a failure is delivered as
    a final frame, mirroring the ``/v1`` stream contract).
    """
    task_id = uuid.uuid4().hex
    text = message_text(req.params)
    try:
        async for event in agent.stream(text, caller=caller, session_id=task_id):
            chunk = _event_text(event)
            if chunk:
                yield _sse(req.id, status_update(task_id, state="working", text=chunk))
        yield _sse(req.id, status_update(task_id, state="completed", final=True))
    except Exception as exc:  # noqa: BLE001 — a mid-stream failure becomes a terminal failed frame
        yield _sse(
            req.id, status_update(task_id, state="failed", text=str(exc), final=True)
        )


def _event_text(event) -> str:
    """Pull the incremental agent text out of one engine event (empty for non-text events)."""
    data = frame_data(event)
    if frame_type(event.type) in ("token", "content"):
        return data.get("content") or data.get("token") or ""
    return ""


def _sse(req_id, result: dict) -> dict:
    """Shape one JSON-RPC response as ``sse-starlette`` ``ServerSentEvent`` fields.

    A2A streams JSON-RPC responses over SSE with only a ``data:`` payload; sse-starlette
    frames it (and adds keepalive comments / disconnect handling) via EventSourceResponse.
    """
    body = JsonRpcResponse.ok(req_id, result).model_dump(by_alias=True)
    return {"data": json.dumps(body)}
