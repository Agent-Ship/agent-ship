"""Tests for the engine seam: the capability gate and unknown-engine handling."""

from __future__ import annotations

import pytest
from agentship.engines.base import EngineCapabilities
from agentship.errors import CapabilityError, EngineNotFoundError
from agentship.runtime import build_agent
from agentship.spec import AgentSpec, MemberSpec


def test_engine_capabilities_defaults_are_all_off():
    """A bare EngineCapabilities declares nothing — every capability defaults off/none."""
    caps = EngineCapabilities()
    assert caps.providers == []
    assert caps.streaming is False
    assert caps.tool_calling is False
    assert caps.hitl is False
    assert caps.durability == "none"
    assert caps.cycles is False
    assert caps.structured_output == "none"
    assert caps.multimodal_in is False
    assert caps.live_bidi is False
    assert caps.multi_agent is False


def test_streaming_on_echo_is_allowed():
    """echo declares streaming, so a streaming spec builds without error."""
    agent = build_agent(AgentSpec(name="a", engine="echo", streaming=True))
    assert agent.spec.streaming is True


def test_output_schema_on_echo_raises_capability_error():
    """echo declares structured_output='none' — an output_schema spec fails fast at build."""
    with pytest.raises(CapabilityError) as exc:
        build_agent(AgentSpec(name="a", engine="echo", output_schema="mypkg:MyModel"))
    assert "structured output" in str(exc.value).lower()


def test_members_on_echo_raises_capability_error():
    """echo is not multi-agent — a spec declaring members fails fast rather than dropping them."""
    spec = AgentSpec(name="team", engine="echo", members=[MemberSpec(name="m1")])
    with pytest.raises(CapabilityError) as exc:
        build_agent(spec)
    assert "multi-agent" in str(exc.value).lower()


def test_durability_request_on_none_engine_raises_capability_error():
    """echo declares durability='none' — a spec asking to checkpoint fails fast at build."""
    spec = AgentSpec(name="a", engine="echo", durability="checkpoint")
    with pytest.raises(CapabilityError) as exc:
        build_agent(spec)
    assert "durability" in str(exc.value).lower()


def test_unknown_engine_raises_engine_not_found():
    """A spec naming an unregistered engine raises EngineNotFoundError listing what's available."""
    with pytest.raises(EngineNotFoundError) as exc:
        build_agent(AgentSpec(name="a", engine="nope"))
    msg = str(exc.value)
    assert "nope" in msg
    assert "echo" in msg  # the message lists the available engines
