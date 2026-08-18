"""Shared route helpers: registry access, the body-size guard, and card/SSE mapping.

Kept in one place so ``agents`` and ``tasks`` routers resolve the registry, enforce the
request-size limit, and shape wire objects identically.
"""

from __future__ import annotations

import json

from agentship.engines.base import Event
from agentship.runtime import RunnableAgent
from fastapi import HTTPException, Request

from ..models.v1 import AgentCard, StreamEvent
from ..registry import AgentRegistry

#: Largest request body the service accepts. A turn's input is text, not a file upload, so
#: a megabyte is generous; anything larger is rejected with 413 before it is read into
#: memory. (Raised here, not in the gateway, so the limit holds gateway-free in dev too.)
MAX_BODY_BYTES = 1_048_576

#: The event types the stream contract allows (mirrors ``StreamEventType``). An engine event
#: whose type is outside this set is surfaced as a ``content`` frame rather than dropped.
_ALLOWED_EVENT_TYPES = frozenset(
    {"session", "token", "content", "tool_call", "tool_result", "guard", "done", "error"}
)


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
        raise HTTPException(
            status_code=413, detail=f"request body exceeds {MAX_BODY_BYTES} bytes"
        )


def agent_card(agent: RunnableAgent) -> AgentCard:
    """Build the discovery :class:`AgentCard` for a built agent from its spec + engine."""
    spec = agent.spec
    caps = agent.engine.capabilities
    return AgentCard(
        name=spec.name,
        description=spec.prompt,
        streaming=spec.streaming or caps.streaming,
        capabilities=caps.model_dump(mode="json"),
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


def sse(event: StreamEvent) -> str:
    """Serialise a :class:`StreamEvent` as one SSE frame (``event:`` + ``data:`` + blank line)."""
    return f"event: {event.type}\ndata: {json.dumps(event.model_dump())}\n\n"
