"""The ``v1`` wire contract: what a client sends and receives over REST / SSE / WS.

These are the stable, versioned shapes the service exposes. Two rules shape them:

* **Identity is never in the body.** :class:`InvokeRequest` carries no ``user_id`` or
  ``tenant_id`` — identity comes only from the authenticated caller, so a client can
  never name a tenant/user it is not. (See :mod:`agentship.tenancy`.)
* **Streaming is a typed event contract**, not raw text: every :class:`StreamEvent`
  carries a ``type`` and a monotonically increasing ``seq`` so a client can order,
  resume, and branch on events (a token vs. a tool call vs. the terminal ``done``).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

#: The kinds of event a stream can carry (SSE ``:stream`` / WS ``/live``):
#: ``session`` (opening frame with ids), ``token`` (an LLM token), ``content`` (a larger
#: content chunk), ``tool_call``/``tool_result`` (a tool round-trip), ``guard`` (a
#: guardrail action), ``done`` (terminal success), ``error`` (terminal failure).
StreamEventType = Literal[
    "session", "token", "content", "tool_call", "tool_result", "guard", "done", "error"
]


class InvokeRequest(BaseModel):
    """One turn's input. Carries **no** identity — that comes from the authenticated caller.

    ``session_id`` threads a multi-turn conversation (omit it to start a new one);
    ``metadata`` is opaque client context echoed into the run; ``stream`` is accepted for
    symmetry but the streaming path is selected by the ``:stream`` endpoint, not this flag.
    """

    model_config = {"extra": "forbid"}

    input: str = Field(..., description="The input text for this turn.")
    session_id: str | None = Field(
        default=None, description="Conversation id to continue; omit to start a new session."
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Opaque client context echoed into the run."
    )
    stream: bool = Field(
        default=False, description="Hint only; use the :stream endpoint for streaming."
    )


class Usage(BaseModel):
    """Token/turn accounting for one run, when the engine reports it."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


class InvokeResponse(BaseModel):
    """The result of a non-streaming ``:invoke`` turn.

    ``resume_token`` is present only for a durable run that paused or can be resumed;
    ``trace_id`` ties the response to its span tree (P07) for debugging.
    """

    agent: str
    session_id: str
    output: Any = None
    resume_token: dict | None = None
    usage: Usage | None = None
    trace_id: str | None = None


class StreamEvent(BaseModel):
    """One streamed event: a typed frame with an ordering sequence number.

    ``seq`` starts at 0 for the opening ``session`` frame and increases by one per event,
    so a client can detect gaps and order frames regardless of transport. ``data`` holds
    the type-specific payload (the token text, the tool name/args, the final output, or
    the error problem detail).
    """

    type: StreamEventType
    seq: int
    data: dict[str, Any] = Field(default_factory=dict)


class AgentCard(BaseModel):
    """The discovery description of one agent (``GET /v1/agents/{name}``).

    Enough for a client to call the agent without out-of-band docs: its name/description,
    whether it streams, its declared capabilities, and the input/output JSON schemas when
    the agent declares them.
    """

    name: str
    description: str | None = None
    streaming: bool = False
    capabilities: dict[str, Any] = Field(default_factory=dict)
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None


#: A durable task's lifecycle state (the ``/v1/tasks`` skeleton; full executor → P11).
TaskStatus = Literal["pending", "running", "completed", "failed", "cancelled"]


class TaskRef(BaseModel):
    """A handle to a durable task: its id, tenant-scoped status, and (later) its result."""

    id: str
    status: TaskStatus
    agent: str
    session_id: str | None = None
    resume_token: dict | None = None
    result: Any = None


class ProblemDetail(BaseModel):
    """An RFC-9457 ``application/problem+json`` error body.

    ``code`` is our stable machine code (e.g. ``invalid_api_key``, ``forbidden``) so a
    client branches on it without parsing prose; ``trace_id`` ties the failure to its span
    tree. No internal detail (stack traces, SQL) is ever placed here.
    """

    model_config = {"extra": "allow"}

    type: str = "about:blank"
    title: str
    status: int
    detail: str | None = None
    code: str | None = None
    trace_id: str | None = None
