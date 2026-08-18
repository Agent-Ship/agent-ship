"""``SpecialistResolver`` picks in-process vs networked, transparently (P05 · C3/C6).

A supervisor calls ``resolve(ref, ctx).send(text)`` the same way whichever side of the wire the
specialist lives on. The remote path is exercised through an injected fake transport so the test
needs no network, and asserts the A2A ``message/send`` envelope plus the tenant/trace propagation.
"""

from __future__ import annotations

import pytest
from agentship.a2a.models import AgentRef, JsonRpcResponse, Message, RemoteSpec
from agentship.a2a.resolver import SpecialistResolver
from agentship.context import Caller, RunContext, RunMode
from agentship.errors import A2AUnavailable


class _FakeAgent:
    """A minimal in-process agent: echoes text through a ``run`` port returning ``.output``."""

    async def run(self, text: str, *, user_id: str = "anon"):
        class _R:
            output = f"local: {text}"

        return _R()


class _RecordingTransport:
    """A fake A2A transport that records the outbound call and returns a canned agent message."""

    def __init__(self, reply: str = "remote reply") -> None:
        self.reply = reply
        self.calls: list[dict] = []

    async def rpc(self, url: str, payload: dict, headers: dict) -> dict:
        """Record the request and answer with a JSON-RPC message result."""
        self.calls.append({"url": url, "payload": payload, "headers": headers})
        return JsonRpcResponse.ok(
            payload["id"], Message.agent(self.reply).model_dump(by_alias=True)
        ).model_dump(by_alias=True)


def _ctx() -> RunContext:
    """A run context for tenant ``acme`` carrying a trace id."""
    return RunContext(
        session_id="s1",
        run_id="r1",
        agent_name="supervisor",
        mode=RunMode.INVOKE,
        caller=Caller(tenant_id="acme", user_id="u1"),
        trace_id="0af7651916cd43dd8448eb211c80319c",
    )


async def test_local_ref_resolves_in_process() -> None:
    """A local ref runs the registered agent in-process — no transport involved."""
    resolver = SpecialistResolver(registry={"billing": _FakeAgent()})
    specialist = resolver.resolve(AgentRef(name="billing", local_ref="billing"), _ctx())
    assert await specialist.send("pay") == "local: pay"


async def test_remote_ref_serialises_message_send() -> None:
    """A remote ref sends an A2A ``message/send`` and returns the agent's text."""
    transport = _RecordingTransport(reply="remote: ok")
    resolver = SpecialistResolver(registry={}, transport=transport)
    ref = AgentRef(name="radiology", remote=RemoteSpec(url="https://rad.internal/a2a/radiology"))
    specialist = resolver.resolve(ref, _ctx())

    assert await specialist.send("scan") == "remote: ok"
    call = transport.calls[0]
    assert call["url"] == "https://rad.internal/a2a/radiology"
    assert call["payload"]["method"] == "message/send"
    assert call["payload"]["params"]["message"]["parts"][0]["text"] == "scan"


async def test_remote_call_propagates_tenant_and_traceparent() -> None:
    """Outbound A2A carries the signed tenant claim and a W3C traceparent (§C6, P07 stitch)."""
    transport = _RecordingTransport()
    resolver = SpecialistResolver(registry={}, transport=transport)
    ref = AgentRef(name="rad", remote=RemoteSpec(url="https://rad/a2a/rad"))
    await resolver.resolve(ref, _ctx()).send("x")

    headers = transport.calls[0]["headers"]
    assert headers["X-AgentShip-Tenant"] == "acme"
    assert headers["traceparent"].startswith("00-0af7651916cd43dd8448eb211c80319c-")


async def test_local_ref_missing_from_registry_raises() -> None:
    """A local ref pointing at an unregistered agent fails fast (not a silent no-op)."""
    resolver = SpecialistResolver(registry={})
    with pytest.raises(Exception, match="no agent registered|billing"):
        resolver.resolve(AgentRef(name="billing", local_ref="billing"), _ctx())


async def test_remote_transport_failure_becomes_a2a_unavailable() -> None:
    """A transport error surfaces as retryable ``A2AUnavailable`` — the caller's graph decides."""

    class _Boom:
        async def rpc(self, url, payload, headers):
            raise ConnectionError("refused")

    resolver = SpecialistResolver(registry={}, transport=_Boom())
    ref = AgentRef(name="rad", remote=RemoteSpec(url="https://rad/a2a/rad"))
    with pytest.raises(A2AUnavailable):
        await resolver.resolve(ref, _ctx()).send("x")
