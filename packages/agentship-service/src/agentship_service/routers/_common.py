"""Shared route helpers: registry access, the body-size guard, and card/SSE mapping.

Kept in one place so ``agents`` and ``tasks`` routers resolve the registry, enforce the
request-size limit, and shape wire objects identically.
"""

from __future__ import annotations

import json
from typing import get_args

from agentship.engines.base import Event
from agentship.runtime import RunnableAgent
from fastapi import HTTPException, Request

from ..models.v1 import AgentCard, StreamEvent, StreamEventType
from ..registry import AgentRegistry

#: Largest request body the service accepts. A turn's input is text, not a file upload, so
#: a megabyte is generous; anything larger is rejected with 413 before it is read into
#: memory. (Raised here, not in the gateway, so the limit holds gateway-free in dev too.)
MAX_BODY_BYTES = 1_048_576

#: The event types the stream contract allows. Derived from ``StreamEventType`` rather than
#: repeated: this was a hand-written copy of the same list, so adding a frame type to the
#: contract left this set behind and the new type was silently downgraded to ``content``.
#: An engine event whose type is outside the set is surfaced as ``content``, not dropped.
_ALLOWED_EVENT_TYPES = frozenset(get_args(StreamEventType))


def get_agents(request: Request) -> AgentRegistry:
    """FastAPI dependency: the :class:`AgentRegistry` mounted on the app."""
    return request.app.state.agents


def resolve_agent(agents: AgentRegistry, name: str) -> RunnableAgent:
    """Return the agent named ``name`` or raise a 404 (rendered as problem+json).

    Authorization runs *before* this (see ``require_scope``), so an unauthorized caller
    gets 403 and never learns whether the agent exists; only an authorized caller can
    distinguish a real agent from a 404.
    """
    try:
        return agents.get(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"no agent named {name!r}") from None


def enforce_body_limit(request: Request) -> None:
    """FastAPI dependency: reject an over-large request body with 413 before parsing it.

    Uses the declared ``Content-Length``; a client that omits it (chunked upload) is still
    bounded by the server's own stream limits. Text turns are small, so this simply stops a
    pathological body from being buffered into memory.
    """
    raw = request.headers.get("content-length")
    if raw is not None and raw.isdigit() and int(raw) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail=f"request body exceeds {MAX_BODY_BYTES} bytes")


def agent_card(agent: RunnableAgent) -> AgentCard:
    """Build the discovery :class:`AgentCard` describing what THIS agent does.

    The engine's capabilities are its ceiling, not this agent's behaviour: a LangGraph agent
    *can* checkpoint, but only one declaring ``durability: checkpoint`` actually does. The card
    used to publish the engine's capabilities verbatim, so every agent advertised durability it
    had not asked for — and a UI rendering that as a badge told users an agent remembered
    conversations when it kept no state at all.

    Only a capability the agent OPTS INTO is overridden. ``durability`` is one: an agent
    checkpoints only if its spec asks to. ``streaming`` is NOT — it is a capability, and
    ``spec.streaming`` is a build-time request the gate checks, not a refusal to stream. I
    briefly reported `spec.streaming and caps.streaming`, which made every agent omitting the
    field advertise streaming: false, and clients fell back to non-streaming calls.
    """
    spec = agent.spec
    caps = agent.engine.capabilities.model_dump(mode="json")
    # Opt-in per agent: report the spec's answer, not the engine's ceiling.
    caps["durability"] = spec.durability
    return AgentCard(
        name=spec.name,
        description=spec.prompt,
        # `exclude_none` so a card shows what the author actually wrote, not every default the
        # model carries — a spec padded with nulls reads as configuration nobody chose.
        spec=spec.model_dump(mode="json", exclude_none=True),
        streaming=agent.engine.capabilities.streaming,
        capabilities=caps,
        input_schema=None,
        output_schema=None,
    )


def frame_data(event: Event) -> dict:
    """Normalise an engine :class:`Event`'s payload into the ``StreamEvent.data`` dict.

    A dict payload passes through; a scalar (e.g. echo's text chunk) is wrapped as
    ``{"content": ...}``; ``None`` becomes an empty dict.
    """
    if event.data is None:
        return {}
    if isinstance(event.data, dict):
        return event.data
    return {"content": event.data}


def frame_type(event_type: str) -> str:
    """Map an engine event type onto the stream contract, defaulting unknowns to ``content``."""
    return event_type if event_type in _ALLOWED_EVENT_TYPES else "content"


def sse(event: StreamEvent) -> dict:
    """Shape a :class:`StreamEvent` as ``sse-starlette`` ``ServerSentEvent`` fields.

    The ``event`` name and JSON ``data`` payload are returned as a dict for
    :class:`~sse_starlette.sse.EventSourceResponse` to frame. sse-starlette owns the wire
    encoding (``event:``/``data:`` lines and the blank-line terminator), the periodic
    keepalive comment, and client-disconnect cancellation — we no longer hand-frame it.
    """
    return {"event": event.type, "data": json.dumps(event.model_dump())}
