"""The A2A HTTP surface: public Agent Cards + the authenticated JSON-RPC endpoint (§C4).

Three routes, all opt-in — only an agent whose spec sets ``a2a.expose: true`` is reachable here;
every other agent is 404 on the network (Layer 0 agents never leak onto the wire):

* ``GET /.well-known/agents.json`` — a public index of the exposed agents.
* ``GET /a2a/{name}/.well-known/agent-card.json`` — the public, capability-honest Agent Card.
* ``POST /a2a/{name}`` — the authenticated JSON-RPC endpoint (``message/send``, ``message/stream``).

The POST route runs under the same P04 auth middleware as ``/v1`` and additionally checks the
distinct ``a2a:invoke`` verb — a caller granted direct invoke is not automatically granted
cross-agent A2A. The two GET routes are public discovery (RFC 8615 ``.well-known``).
"""

from __future__ import annotations

from agentship.a2a.card import build_agent_card
from agentship.a2a.models import JsonRpcRequest
from agentship.context import Caller
from agentship.runtime import RunnableAgent
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

from ..a2a import handle_rpc, stream_rpc
from ..middleware import require_scope
from ..registry import AgentRegistry
from ._common import get_agents, resolve_agent

router = APIRouter(tags=["a2a"])


def _exposed_or_404(agents: AgentRegistry, name: str) -> RunnableAgent:
    """Return agent ``name`` only if it opted into A2A exposure; otherwise 404.

    A non-exposed (Layer 0) agent must be indistinguishable from a missing one on the network, so
    both collapse to 404 — the A2A surface simply does not exist for it.
    """
    agent = resolve_agent(agents, name)  # 404 if there is no such agent at all
    expose = getattr(agent.spec, "a2a", None)
    if expose is None or not expose.expose:
        raise HTTPException(status_code=404, detail=f"agent {name!r} is not exposed over A2A")
    return agent


def _base_url(request: Request) -> str:
    """The service root (scheme://host) the Agent Card advertises its endpoint under."""
    return str(request.base_url).rstrip("/")


@router.get("/.well-known/agents.json", include_in_schema=False)
async def agents_index(request: Request, agents: AgentRegistry = Depends(get_agents)) -> dict:
    """Public index of every A2A-exposed agent (name + card URL)."""
    root = _base_url(request)
    exposed = [a for a in agents if getattr(a.spec, "a2a", None) and a.spec.a2a.expose]
    return {
        "agents": [
            {"name": a.spec.name, "url": f"{root}/a2a/{a.spec.name}"} for a in exposed
        ]
    }


@router.get("/a2a/{name}/.well-known/agent-card.json", include_in_schema=False)
async def agent_card(
    name: str, request: Request, agents: AgentRegistry = Depends(get_agents)
) -> JSONResponse:
    """Public, capability-honest Agent Card for one exposed agent."""
    agent = _exposed_or_404(agents, name)
    card = build_agent_card(
        agent.spec,
        agent.engine.capabilities,
        base_url=_base_url(request),
        security=agent.spec.a2a.security,
    )
    return JSONResponse(card.model_dump(mode="json", by_alias=True))


@router.post("/a2a/{name}")
async def rpc(
    name: str,
    request: Request,
    caller: Caller = Depends(require_scope("a2a:invoke")),
    agents: AgentRegistry = Depends(get_agents),
):
    """Serve one A2A JSON-RPC call for agent ``name`` (``message/send`` or ``message/stream``).

    ``message/stream`` returns an SSE stream of task-status frames; every other method returns a
    single JSON-RPC response. The distinct ``a2a:invoke`` scope is enforced by the dependency
    before we touch the agent.
    """
    agent = _exposed_or_404(agents, name)
    req = JsonRpcRequest.model_validate(await request.json())
    if req.method == "message/stream":
        return EventSourceResponse(
            stream_rpc(agent, caller, req)
        )
    response = await handle_rpc(agent, caller, req)
    return JSONResponse(response.model_dump(mode="json", by_alias=True))
