"""``build_agent_card`` reflects the engine's *real* capabilities honestly (P05 · C4)."""

from __future__ import annotations

from agentship.a2a.card import build_agent_card
from agentship.engines.base import EngineCapabilities
from agentship.spec import AgentSpec


def test_card_streaming_true_only_when_engine_streams() -> None:
    """``capabilities.streaming`` follows the engine, not wishful config."""
    spec = AgentSpec(name="triage", engine="echo", prompt="routes messages", streaming=True)
    caps = EngineCapabilities(streaming=True)
    card = build_agent_card(spec, caps, base_url="https://host")
    assert card.capabilities.streaming is True
    # url is the agent's A2A base path under the service.
    assert card.url == "https://host/a2a/triage"
    assert card.description == "routes messages"


def test_card_streaming_false_when_engine_cannot_stream() -> None:
    """A non-streaming engine yields ``streaming: false`` even if nothing else changed."""
    spec = AgentSpec(name="q", engine="echo", prompt="answers")
    caps = EngineCapabilities(streaming=False)
    card = build_agent_card(spec, caps, base_url="https://host/")
    assert card.capabilities.streaming is False
    # A trailing slash on base_url does not double up.
    assert card.url == "https://host/a2a/q"


def test_card_advertises_declared_security_schemes() -> None:
    """The card's ``securitySchemes``/``security`` come from the agent's declared schemes."""
    spec = AgentSpec(name="triage", engine="echo")
    caps = EngineCapabilities(streaming=True)
    card = build_agent_card(spec, caps, base_url="https://host", security=["oauth2"])
    assert "oauth2" in card.security_schemes
    assert card.security == [{"oauth2": []}]


def test_card_is_schema_valid_json() -> None:
    """The serialised card carries the required A2A fields with camelCase names."""
    spec = AgentSpec(name="triage", engine="echo")
    caps = EngineCapabilities(streaming=True)
    dumped = build_agent_card(spec, caps, base_url="https://host").model_dump(
        mode="json", by_alias=True
    )
    for required in ("name", "url", "version", "protocolVersion", "capabilities"):
        assert required in dumped
