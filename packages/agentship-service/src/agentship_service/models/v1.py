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

from agentship.engines.base import ResumeToken
from pydantic import BaseModel, Field

#: The kinds of event a stream can carry (SSE ``:stream`` / WS ``/live``):
#: ``session`` (opening frame with ids), ``token`` (an LLM token), ``content`` (a larger
#: content chunk), ``reasoning`` (the model's thinking, when a reasoning model emits it),
#: ``tool_call``/``tool_result`` (a tool round-trip), ``guard`` (a guardrail action),
#: ``done`` (terminal success), ``error`` (terminal failure).
#:
#: ``reasoning`` is deliberately not ``content``: thinking is not the answer, and a client
#: that cannot tell them apart has no way to render one as a collapsed aside and the other
#: as the reply. It is also the channel a UI uses to show that a slow reasoning model is
#: working rather than hung.
StreamEventType = Literal[
    "session",
    "token",
    "content",
    "reasoning",
    "tool_call",
    "tool_result",
    "guard",
    "done",
    "error",
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


class ResumeRequest(BaseModel):
    """Continue a run that paused (HITL) or crashed, using the token a prior turn returned.

    ``resume_token`` is the ``{engine, blob}`` value handed back in an
    :class:`InvokeResponse` — echo it back unchanged. ``resume_value`` is the human's
    decision for a run paused on an ``interrupt`` (e.g. ``{"approved": true}``); omit it
    for a plain crash-resume.

    ``session_id`` is required, unlike on :class:`InvokeRequest`: a resume replays a
    specific checkpoint thread, so there is no sensible id to mint — a fresh one would
    silently resume nothing. Identity still comes from the authenticated caller, never
    the body.
    """

    model_config = {"extra": "forbid"}

    # Typed, not `dict`: the token is validated at the API boundary, so a malformed one is a
    # 422 problem document naming the bad field. As a bare `dict` it reached
    # `ResumeToken.model_validate` inside the handler, where the ValidationError was nobody's
    # registered error — a 500 that told the caller their own bad input was a server fault.
    resume_token: ResumeToken = Field(
        ..., description="The {engine, blob} token from a prior turn."
    )
    session_id: str = Field(..., description="The paused run's session (its checkpoint thread).")
    resume_value: Any | None = Field(
        default=None, description='The human\'s decision, e.g. {"approved": true}.'
    )


class Timings(BaseModel):
    """How long one turn took, in milliseconds.

    Latency is a first-class part of the answer, not a debug log: a reply that is correct and
    slow is a different product from one that is correct and fast, and you cannot tune what the
    API will not tell you. ``ttft_ms`` is tracked apart from ``total_ms`` because the gap
    between them is what streaming buys — collapsing the two hides whether a turn is overlapped
    or merely quick.

    A field is ``None`` when it was not measured, never ``0`` — a zero here would be a
    measurement nobody took.
    """

    #: Time until the first content reached the client. What a waiting human actually feels.
    ttft_ms: float | None = None
    #: Wall clock for the whole turn.
    total_ms: float | None = None


class Usage(BaseModel):
    """Token/turn accounting for one run, when the engine reports it."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


class InvokeResponse(BaseModel):
    """The result of a non-streaming ``:invoke`` turn.

    ``resume_token`` is present for ANY durable run — it is the crash-resume handle, so a
    run that finished normally carries one too. ``paused`` is the field that says a human is
    actually being waited on; a client must branch on that, never on the token's presence.
    ``interrupt`` carries what the run is asking (the payload the node passed to
    ``interrupt(...)``), so a client can render the real question instead of a generic
    "this run paused". ``trace_id`` ties the response to its span tree (P07) for debugging.

    A paused run may ALSO have produced output — it can say something before it asks — so
    neither field implies anything about the other.
    """

    agent: str
    session_id: str
    output: Any = None
    resume_token: dict | None = None
    paused: bool = False
    interrupt: dict | None = None
    usage: Usage | None = None
    #: Where this turn's time went. See :class:`Timings`.
    timings: Timings | None = None
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
    #: The agent's declared spec, as committed. This is the contract the agent IS — its
    #: model, tools, members and voice block — so a client can show what it is talking to
    #: without a second endpoint or a trip to the repository. Secrets never live in a spec
    #: (they come from the environment), which is what makes it safe to publish here.
    spec: dict[str, Any] | None = None


class TaskCreateRequest(BaseModel):
    """Enqueue a durable task: which agent to run and its input. Identity is the caller's.

    Like :class:`InvokeRequest` this carries no ``user_id``/``tenant_id`` — the task is
    owned by the authenticated caller's tenant so it can never be created for another.
    """

    model_config = {"extra": "forbid"}

    agent: str = Field(..., description="Name of the agent to run for this task.")
    input: str = Field(..., description="The input text for the task's first turn.")
    session_id: str | None = Field(
        default=None, description="Conversation id to continue; omit to start a new one."
    )


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
