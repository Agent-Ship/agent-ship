"""Schema drift guard: our A2A wire JSON must validate against the official a2a-sdk schema.

We deliberately keep our own thin, vendor-free Pydantic wire models (``agentship.a2a.models``)
instead of adopting ``a2a-sdk``'s types wholesale: a2a-sdk 1.x is protobuf-first (``a2a.types``
is compiled protobuf), and its only Pydantic/JSON representation is the legacy
``a2a.compat.v0_3`` shim — adopting either would force grpc/protobuf lock-in and protobuf-JSON
semantics into our clean Pydantic/FastAPI JSON service. So instead of importing their models,
we *validate against* them: every AgentCard / Message / status-update / result JSON we emit is
parsed by a2a-sdk's own schema here, so if we ever drift from the A2A spec this fails CI.

Runs only under the ``agentship-service[a2a]`` extra (a2a-sdk is a heavy grpc/protobuf dep, not
a hard requirement); the base install skips it. This guard already caught three real spec gaps
(``Message.messageId`` and ``TaskStatusUpdateEvent.contextId`` being required; the ``apiKey``
scheme needing ``in``/``name``), which the models now emit correctly.
"""

from __future__ import annotations

import pytest

a2a_types = pytest.importorskip(
    "a2a.compat.v0_3.types",
    reason="a2a-sdk not installed — run under agentship-service[a2a] to exercise the guard",
)

from agentship.a2a.card import build_agent_card  # noqa: E402
from agentship.a2a.models import Message  # noqa: E402
from agentship.engines.base import EngineCapabilities  # noqa: E402
from agentship.spec import AgentSpec  # noqa: E402
from agentship_service.a2a.mapping import result_message, status_update  # noqa: E402

_SPEC = AgentSpec(name="demo", prompt="You are a demo agent.")
_CAPS = EngineCapabilities(streaming=True)


def _wire(model) -> dict:
    """Serialise a wire model exactly as the service emits it (aliased, no nulls)."""
    return model.model_dump(by_alias=True, exclude_none=True)


def test_agent_card_matches_a2a_schema() -> None:
    """A plain streaming AgentCard validates against a2a-sdk's AgentCard schema."""
    card = build_agent_card(_SPEC, _CAPS, base_url="https://host")
    a2a_types.AgentCard.model_validate(_wire(card))


def test_agent_card_with_api_key_security_matches_a2a_schema() -> None:
    """An AgentCard advertising the apiKey scheme carries the spec-required in/name."""
    card = build_agent_card(_SPEC, _CAPS, base_url="https://host", security=["apiKey"])
    a2a_types.AgentCard.model_validate(_wire(card))


def test_message_matches_a2a_schema() -> None:
    """Both message roles carry the required messageId and validate as A2A Messages."""
    a2a_types.Message.model_validate(_wire(Message.agent("hello")))
    a2a_types.Message.model_validate(_wire(Message.user("hi")))


def test_result_message_matches_a2a_schema() -> None:
    """The non-streamed message/send result validates as an A2A Message."""
    a2a_types.Message.model_validate(result_message("the answer"))


def test_status_update_matches_a2a_schema() -> None:
    """Each streamed frame validates as an A2A TaskStatusUpdateEvent (taskId + contextId)."""
    working = status_update("task-1", "ctx-1", state="working", text="partial")
    a2a_types.TaskStatusUpdateEvent.model_validate(working)
    done = status_update("task-1", "ctx-1", state="completed", final=True)
    a2a_types.TaskStatusUpdateEvent.model_validate(done)


@pytest.mark.xfail(reason="oauth2/mtls card schemes not yet fully spec-conformant — task #26")
def test_oauth2_card_security_matches_a2a_schema() -> None:
    """oauth2 needs OAuth2SecurityScheme flows we don't model yet (tracked follow-up)."""
    card = build_agent_card(_SPEC, _CAPS, base_url="https://host", security=["oauth2"])
    a2a_types.AgentCard.model_validate(_wire(card))
