"""Tests for the engine seam: the capability gate and unknown-engine handling."""

from __future__ import annotations

import pytest
from agentship.engines.base import EngineCapabilities, Modality
from agentship.errors import CapabilityError, EngineNotFoundError
from agentship.runtime import build_agent
from agentship.spec import AgentSpec, MemberSpec


def test_engine_capabilities_defaults_are_all_off():
    """A bare EngineCapabilities declares nothing — every capability defaults off/none."""
    caps = EngineCapabilities()
    assert caps.providers == set()
    assert caps.streaming is False
    assert caps.tool_calling is False
    assert caps.hitl == "none"
    assert caps.durability == "none"
    assert caps.cycles is False
    assert caps.structured_output == "none"
    assert caps.multimodal_in == set()
    assert caps.live_bidi is False
    assert caps.multi_agent is False


def test_providers_is_a_set_of_strings():
    """``providers`` is a ``set[str]`` (canonical §3.1), not a list — order-free membership."""
    caps = EngineCapabilities(providers={"openai", "anthropic"})
    assert caps.providers == {"openai", "anthropic"}


def test_hitl_is_a_three_valued_literal():
    """``hitl`` widens to Literal[none|interrupt|deferred_tool] (canonical §3.1)."""
    assert EngineCapabilities(hitl="interrupt").hitl == "interrupt"
    assert EngineCapabilities(hitl="deferred_tool").hitl == "deferred_tool"
    with pytest.raises(ValueError):
        EngineCapabilities(hitl="yes")


def test_multimodal_in_is_a_set_of_modalities():
    """``multimodal_in`` widens to ``set[Modality]`` (canonical §3.1)."""
    caps = EngineCapabilities(multimodal_in={Modality.IMAGE, Modality.AUDIO})
    assert caps.multimodal_in == {Modality.IMAGE, Modality.AUDIO}


def test_modality_enum_has_the_canonical_members():
    """``Modality`` is the canonical text/image/audio/video/pdf StrEnum."""
    assert {m.value for m in Modality} == {"text", "image", "audio", "video", "pdf"}


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
