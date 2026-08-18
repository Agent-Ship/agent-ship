"""Phase 05 conformance cells: A2A card, specialist transparency, interop optionality (§11).

Named cells that pin P05's promises engine-agnostically (over the in-memory echo engine, no
gateway, no network — the CI-gate tier):

* ``a2a_server_card`` — an exposed agent serves a schema-valid Agent Card whose ``capabilities``
  match its real ``EngineCapabilities`` (streaming true only when the engine streams), and a
  non-exposed agent has no card at all.
* ``a2a_specialist_transparency`` — the same supervisor call site produces an equivalent result
  whether the specialist is resolved in-process or over A2A (modulo the transport hop), proving the
  ``kit.specialist(name).send(text)`` seam never branches on transport.
* ``interop_optional`` — a Layer 0 agent (no ``a2a`` block, no networked members) builds and runs
  with the interop layer untouched, so the whole ``agentship.a2a`` subtree is optional by default.

The virtual-MCP federation parity cell (``mcp_federation_virtual_mcp``) is a live-gateway cell: it
requires a running agentgateway process and is exercised by the ``deploy/agentgateway`` recipe's
integration profile, not this CI-gate tier — see that folder's README. It is listed here so the
matrix records it as deferred-with-cause rather than silently missing.
"""

from __future__ import annotations

import json

from agentship.a2a.models import AgentRef, JsonRpcResponse, Message, RemoteSpec
from agentship.a2a.resolver import SpecialistResolver
from agentship.auth import ApiKeyAuthProvider, EnvApiKeyStore
from agentship.context import Caller, RunContext, RunMode
from agentship.runtime import build_agent
from agentship.spec import AgentSpec
from agentship_service import AgentRegistry, create_app
from fastapi.testclient import TestClient

#: The A2A Agent Card fields our generated card must always carry (camelCase on the wire).
_REQUIRED_CARD_FIELDS = frozenset(
    {"name", "url", "version", "protocolVersion", "capabilities", "defaultInputModes"}
)

_KEYS = json.dumps([{"key": "k", "user": "u", "tenant": "acme", "scopes": ["*"]}])


def _ctx() -> RunContext:
    """A run context for tenant ``acme`` (identity the specialist call runs under)."""
    return RunContext(
        session_id="s",
        run_id="r",
        agent_name="supervisor",
        mode=RunMode.INVOKE,
        caller=Caller(tenant_id="acme", user_id="u"),
    )


def test_a2a_server_card() -> None:
    """Cell ``a2a_server_card``: exposed agent serves an honest card; non-exposed has none."""
    agents = AgentRegistry(
        [
            build_agent(
                AgentSpec(
                    name="triage",
                    engine="echo",
                    streaming=True,
                    a2a={"expose": True, "security": ["apiKey"]},
                )
            ),
            build_agent(AgentSpec(name="internal", engine="echo")),
        ]
    )
    client = TestClient(
        create_app(auth=ApiKeyAuthProvider(EnvApiKeyStore(raw=_KEYS)), agents=agents)
    )

    card = client.get("/a2a/triage/.well-known/agent-card.json")
    assert card.status_code == 200
    body = card.json()
    assert _REQUIRED_CARD_FIELDS <= set(body)
    # Honest capabilities: the echo engine streams, so the card says streaming.
    assert body["capabilities"]["streaming"] is True
    assert "apiKey" in body["securitySchemes"]

    # A non-exposed agent is invisible on the A2A surface.
    assert client.get("/a2a/internal/.well-known/agent-card.json").status_code == 404


async def test_a2a_specialist_transparency() -> None:
    """Cell ``a2a_specialist_transparency``: in-process and remote resolve to the same call site."""
    # In-process specialist: a built echo agent invoked through its run port.
    local_agent = build_agent(AgentSpec(name="worker", engine="echo"))
    local = SpecialistResolver(registry={"worker": local_agent}).resolve(
        AgentRef(name="worker", local_ref="worker"), _ctx()
    )
    local_reply = await local.send("ping")

    # Remote specialist: an A2A peer whose server returns the *same* echo output shape. The fake
    # transport stands in for the network hop; the supervisor's call site is byte-identical.
    class _EchoPeer:
        async def rpc(self, url: str, payload: dict, headers: dict) -> dict:
            text = payload["params"]["message"]["parts"][0]["text"]
            reply = Message.agent(f"echo: {text}").model_dump(by_alias=True)
            return JsonRpcResponse.ok(payload["id"], reply).model_dump(by_alias=True)

    remote = SpecialistResolver(registry={}, transport=_EchoPeer()).resolve(
        AgentRef(name="worker", remote=RemoteSpec(url="https://peer/a2a/worker")), _ctx()
    )
    remote_reply = await remote.send("ping")

    # Identical call site, equivalent result — the transport is invisible to the caller.
    assert local_reply == remote_reply == "echo: ping"


def test_interop_optional() -> None:
    """Cell ``interop_optional``: a Layer 0 agent builds/runs with no ``a2a`` config in play."""
    spec = AgentSpec(name="plain", engine="echo")
    assert spec.a2a is None  # no interop block
    agent = build_agent(spec)
    # It is not exposed and not networked — the default runtime is untouched by P05.
    assert getattr(agent.spec, "a2a", None) is None
