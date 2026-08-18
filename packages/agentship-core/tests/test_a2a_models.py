"""``AgentRef``/``RemoteSpec`` validation and the A2A wire-model shapes (P05 · C3, C4)."""

from __future__ import annotations

import pytest
from agentship.a2a.models import (
    AgentCard,
    AgentRef,
    JsonRpcRequest,
    JsonRpcResponse,
    Message,
    RemoteSpec,
    TextPart,
)
from agentship.errors import CapabilityError


def test_local_ref_resolves_as_in_process() -> None:
    """A ref with only ``local_ref`` is in-process (``is_remote`` is false)."""
    ref = AgentRef(name="billing", local_ref="billing_agent")
    assert not ref.is_remote()
    assert ref.remote is None


def test_remote_ref_is_networked() -> None:
    """A ref carrying a ``RemoteSpec`` is networked (``is_remote`` is true)."""
    ref = AgentRef(name="radiology", remote=RemoteSpec(url="https://rad.internal/a2a/radiology"))
    assert ref.is_remote()
    assert ref.remote is not None
    # The card path defaults to the A2A well-known location.
    assert ref.remote.card_path == "/.well-known/agent-card.json"


def test_ref_requires_exactly_one_target() -> None:
    """Neither or both of local/remote is a load-time ``CapabilityError`` (§C3)."""
    with pytest.raises(CapabilityError, match="exactly one"):
        AgentRef(name="x")
    with pytest.raises(CapabilityError, match="exactly one"):
        AgentRef(
            name="x",
            local_ref="y",
            remote=RemoteSpec(url="https://z/a2a/z"),
        )


def test_agent_card_is_json_serialisable_with_a2a_field_names() -> None:
    """The card serialises with A2A's camelCase field names (``protocolVersion`` etc.)."""
    card = AgentCard(
        name="triage",
        description="routes messages",
        url="https://host/a2a/triage",
        version="1.0.0",
    )
    dumped = card.model_dump(mode="json", by_alias=True)
    assert dumped["protocolVersion"]
    assert dumped["capabilities"]["streaming"] is False
    assert dumped["defaultInputModes"] == ["text/plain"]


def test_jsonrpc_request_and_response_roundtrip() -> None:
    """A ``message/send`` request and a result response keep the JSON-RPC envelope."""
    req = JsonRpcRequest(
        id="1",
        method="message/send",
        params={"message": Message.user("hello").model_dump(by_alias=True)},
    )
    assert req.jsonrpc == "2.0"
    resp = JsonRpcResponse(id="1", result={"ok": True})
    assert resp.jsonrpc == "2.0"
    assert resp.error is None


def test_message_user_helper_builds_a_single_text_part() -> None:
    """``Message.user`` wraps text as one ``TextPart`` with role ``user``."""
    msg = Message.user("hi there")
    assert msg.role == "user"
    assert msg.parts == [TextPart(text="hi there")]
    assert msg.text() == "hi there"
