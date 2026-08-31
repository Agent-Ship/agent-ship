"""Agent invocation and discovery: ``:invoke``, ``:stream`` (SSE), and the agent catalog.

Every invocation runs inside the request's authenticated caller: ``require_scope`` proves
the caller holds ``agent:{name}:invoke`` and hands the :class:`Caller` to the handler,
which threads it into the run so the turn is scoped to the caller's tenant and scopes.
Discovery (``GET /v1/agents`` and ``/{name}``) needs only a valid credential — it lists
the service's catalog, not any tenant-owned resource.
"""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import contextmanager

from agentship.context import Caller
from agentship.engines.base import Result, ResumeToken
from agentship.errors import CapabilityError
from fastapi import APIRouter, Depends
from sse_starlette.sse import EventSourceResponse

from ..context import current_trace_id
from ..middleware import get_caller, require_scope
from ..models.v1 import AgentCard, InvokeRequest, InvokeResponse, ResumeRequest, StreamEvent
from ..registry import AgentRegistry
from ._common import (
    agent_card,
    enforce_body_limit,
    frame_data,
    frame_type,
    get_agents,
    resolve_agent,
    sse,
)

router = APIRouter(prefix="/v1/agents", tags=["agents"])


#: One line per served turn, so a running deployment shows what an agent actually did — not
#: just uvicorn's access lines. Silent unless the tree is configured (``agentship serve``
#: does that; importing the library still prints nothing).
logger = logging.getLogger("agentship.service")


def _invoke_response(name: str, session_id: str, result: Result) -> InvokeResponse:
    """Shape an engine :class:`Result` into the wire :class:`InvokeResponse`."""
    return InvokeResponse(
        agent=name,
        session_id=session_id,
        output=result.output,
        resume_token=result.resume_token.model_dump() if result.resume_token else None,
        trace_id=current_trace_id(),
    )


@contextmanager
def log_turn(verb: str, name: str, caller: Caller, session_id: str):
    """Log one served turn's start and outcome, with who asked and how long it took.

    Emits ``verb agent=… tenant=… session=…`` on entry and a matching ``ok``/``failed`` line
    with the elapsed milliseconds on exit, so a reader can see the turn happen, attribute it
    to a tenant, and spot a slow or failing agent without turning on tracing. The exception is
    logged and re-raised unchanged — this observes, it never swallows.
    """
    started = time.monotonic()
    logger.info("%s agent=%s tenant=%s session=%s", verb, name, caller.tenant_id, session_id)
    try:
        yield
    except BaseException as exc:
        elapsed = (time.monotonic() - started) * 1000
        logger.warning(
            "%s agent=%s session=%s failed in %.0fms: %s: %s",
            verb,
            name,
            session_id,
            elapsed,
            type(exc).__name__,
            exc,
        )
        raise
    else:
        elapsed = (time.monotonic() - started) * 1000
        logger.info("%s agent=%s session=%s ok in %.0fms", verb, name, session_id, elapsed)


@router.post("/{name}:invoke", response_model=InvokeResponse)
async def invoke(
    name: str,
    body: InvokeRequest,
    caller: Caller = Depends(require_scope("invoke")),
    agents: AgentRegistry = Depends(get_agents),
    _limit: None = Depends(enforce_body_limit),
) -> InvokeResponse:
    """Run one non-streaming turn of agent ``name`` as the authenticated caller.

    The session id is echoed back (minted here when the client omits one) so a client can
    thread the next turn. Identity never comes from the body — it is the proven caller.
    """
    agent = resolve_agent(agents, name)
    session_id = body.session_id or uuid.uuid4().hex
    with log_turn("invoke", name, caller, session_id):
        result = await agent.run(body.input, caller=caller, session_id=session_id)
    return _invoke_response(name, session_id, result)


@router.post("/{name}:resume", response_model=InvokeResponse)
async def resume(
    name: str,
    body: ResumeRequest,
    caller: Caller = Depends(require_scope("invoke")),
    agents: AgentRegistry = Depends(get_agents),
    _limit: None = Depends(enforce_body_limit),
) -> InvokeResponse:
    """Continue a paused or crashed run of agent ``name`` from the token a prior turn returned.

    This is what makes the ``resume_token`` in an :class:`InvokeResponse` usable. Without
    it the service handed out a token no endpoint accepted, so a human-in-the-loop agent
    could pause over HTTP and never be resumed over HTTP.

    Gated by the same ``agent:{name}:invoke`` scope as ``:invoke`` — a resume *is* running
    the agent, so it must not be cheaper to authorize. A token minted by a different engine,
    or a resume on an engine that is not durable, raises rather than pretending to continue.
    """
    agent = resolve_agent(agents, name)
    with log_turn("resume", name, caller, body.session_id):
        result = await agent.resume(
            ResumeToken.model_validate(body.resume_token),
            resume_value=body.resume_value,
            caller=caller,
            session_id=body.session_id,
        )
    return _invoke_response(name, body.session_id, result)


@router.post("/{name}:stream")
async def stream(
    name: str,
    body: InvokeRequest,
    caller: Caller = Depends(require_scope("invoke")),
    agents: AgentRegistry = Depends(get_agents),
    _limit: None = Depends(enforce_body_limit),
) -> EventSourceResponse:
    """Stream a turn of agent ``name`` as Server-Sent typed :class:`StreamEvent` frames.

    The first frame is a ``session`` frame carrying the ids; each engine event follows with
    a monotonically increasing ``seq``; a mid-stream failure is delivered as a terminal
    ``error`` frame (the HTTP status was already 200 once streaming began). An agent whose
    engine cannot stream is rejected up front with 400 rather than opening an empty stream.
    """
    agent = resolve_agent(agents, name)
    if not agent.engine.capabilities.streaming:
        raise CapabilityError(f"agent {name!r} does not support streaming")
    session_id = body.session_id or uuid.uuid4().hex

    async def frames():
        """Yield the SSE frames for this turn: session, then engine events, then errors."""
        logger.info("stream agent=%s tenant=%s session=%s", name, caller.tenant_id, session_id)
        started = time.monotonic()
        seq = 0
        yield sse(
            StreamEvent(type="session", seq=seq, data={"session_id": session_id, "agent": name})
        )
        seq += 1
        try:
            async for event in agent.stream(body.input, caller=caller, session_id=session_id):
                yield sse(StreamEvent(type=frame_type(event.type), seq=seq, data=frame_data(event)))
                seq += 1
        except Exception as exc:  # noqa: BLE001 — a mid-stream failure becomes an error frame
            logger.warning(
                "stream agent=%s session=%s failed after %d frame(s): %s: %s",
                name,
                session_id,
                seq,
                type(exc).__name__,
                exc,
            )
            yield sse(StreamEvent(type="error", seq=seq, data={"detail": str(exc)}))
        else:
            elapsed = (time.monotonic() - started) * 1000
            logger.info(
                "stream agent=%s session=%s ok in %.0fms (%d frames)",
                name,
                session_id,
                elapsed,
                seq,
            )

    return EventSourceResponse(frames())


@router.get("", response_model=list[AgentCard])
async def list_agents(
    _caller: Caller = Depends(get_caller),
    agents: AgentRegistry = Depends(get_agents),
) -> list[AgentCard]:
    """List every agent the service exposes as a discovery :class:`AgentCard`."""
    return [agent_card(agent) for agent in agents]


@router.get("/{name}", response_model=AgentCard)
async def get_agent(
    name: str,
    _caller: Caller = Depends(get_caller),
    agents: AgentRegistry = Depends(get_agents),
) -> AgentCard:
    """Return the discovery :class:`AgentCard` for one agent (404 if there is no such agent)."""
    return agent_card(resolve_agent(agents, name))
