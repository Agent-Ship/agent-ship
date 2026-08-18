"""Translate between A2A wire shapes and AgentShip's run inputs/outputs (§C3 mapping).

One small module so the request handler and the stream handler map identically:

* inbound — an A2A ``message/send`` param block → the plain text an engine's ``run``/``stream``
  consumes (:func:`message_text`);
* outbound — an engine :class:`~agentship.engines.base.Result` → an A2A agent ``Message``
  (:func:`result_message`), and a streamed engine event → an A2A ``TaskStatusUpdate`` frame
  (:func:`status_update`).

Kept deliberately thin: A2A models the world as Messages/Tasks; we model it as run/stream, and the
only shapes we translate are the ones we actually serve.
"""

from __future__ import annotations

from typing import Any

from agentship.a2a.models import Message


def message_text(params: dict[str, Any]) -> str:
    """Extract the user text from a ``message/send`` param block.

    A2A carries the turn as ``params.message.parts[]``; we speak text parts, so this joins their
    ``text`` into the single string the engine runs on. A missing/empty message yields ``""`` — an
    empty turn, not a crash.
    """
    raw = params.get("message") or {}
    parts = raw.get("parts") or []
    return "".join(part.get("text", "") for part in parts if part.get("kind", "text") == "text")


def result_message(output: Any) -> dict[str, Any]:
    """Shape an engine result's ``output`` into an A2A agent ``Message`` dict.

    ``message/send`` returns a Message when the turn completes synchronously; the output is
    rendered as one agent text part (a non-string output is stringified so the wire stays valid).
    """
    text = output if isinstance(output, str) else str(output)
    return Message.agent(text).model_dump(by_alias=True)


def status_update(
    task_id: str, *, state: str, text: str = "", final: bool = False
) -> dict[str, Any]:
    """Build an A2A ``TaskStatusUpdate`` result for one streamed step.

    ``state`` is the A2A task state (``working`` while tokens flow, ``completed``/``failed`` at the
    end); ``text`` carries the incremental agent text for this step; ``final`` marks the last frame
    so a client knows the stream is done. This is the streamed analogue of :func:`result_message`.
    """
    update: dict[str, Any] = {
        "kind": "status-update",
        "taskId": task_id,
        "status": {"state": state},
        "final": final,
    }
    if text:
        update["status"]["message"] = Message.agent(text).model_dump(by_alias=True)
    return update
