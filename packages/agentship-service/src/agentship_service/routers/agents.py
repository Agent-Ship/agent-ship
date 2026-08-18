"""Agent invocation and discovery: ``:invoke``, ``:stream`` (SSE), and the agent catalog.

Every invocation runs inside the request's authenticated caller: ``require_scope`` proves
the caller holds ``agent:{name}:invoke`` and hands the :class:`Caller` to the handler,
which threads it into the run so the turn is scoped to the caller's tenant and scopes.
Discovery (``GET /v1/agents`` and ``/{name}``) needs only a valid credential — it lists
the service's catalog, not any tenant-owned resource.
"""

from __future__ import annotations

import uuid

from agentship.context import Caller
from agentship.engines.base import Result
from agentship.errors import CapabilityError
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from ..context import current_trace_id
from ..middleware import get_caller, require_scope
from ..models.v1 import AgentCard, InvokeRequest, InvokeResponse, StreamEvent
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


def _invoke_response(name: str, session_id: str, result: Result) -> InvokeResponse:
    """Shape an engine :class:`Result` into the wire :class:`InvokeResponse`."""
    return InvokeResponse(
        agent=name,
        session_id=session_id,
        output=result.output,
        resume_token=result.resume_token.model_dump() if result.resume_token else None,
        trace_id=current_trace_id(),
    )


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
    result = await agent.run(body.input, caller=caller, session_id=session_id)
    return _invoke_response(name, session_id, result)


@router.post("/{name}:stream")
async def stream(
    name: str,
    body: InvokeRequest,
    caller: Caller = Depends(require_scope("invoke")),
    agents: AgentRegistry = Depends(get_agents),
    _limit: None = Depends(enforce_body_limit),
) -> StreamingResponse:
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
            yield sse(StreamEvent(type="error", seq=seq, data={"detail": str(exc)}))

    return StreamingResponse(frames(), media_type="text/event-stream")


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
