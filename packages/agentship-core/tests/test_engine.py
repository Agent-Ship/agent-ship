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
    """``Modality`` is the canonical text/image/audio/video/pdf/file StrEnum (DESIGN §3.1)."""
    assert {m.value for m in Modality} == {"text", "image", "audio", "video", "pdf", "file"}


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


def test_assert_supports_spec_method_gates_a_spec():
    """``EngineCapabilities.assert_supports_spec`` is the canonical §13.5 spec gate.

    echo declares no structured output, so a spec with an output_schema must raise.
    """
    caps = EngineCapabilities(streaming=True)
    ok_spec = AgentSpec(name="a", engine="echo", streaming=True)
    caps.assert_supports_spec(ok_spec)  # declared → no raise
    bad_spec = AgentSpec(name="a", engine="echo", output_schema="mypkg:M")
    with pytest.raises(CapabilityError):
        caps.assert_supports_spec(bad_spec)


def test_assert_spec_supported_wrapper_still_delegates():
    """The free ``assert_spec_supported`` wrapper still works (imported by the CLI)."""
    from agentship.engines.base import assert_spec_supported
    from agentship.engines.echo import EchoEngine

    engine = EchoEngine()
    assert_spec_supported(engine, AgentSpec(name="a", engine="echo", streaming=True))
    with pytest.raises(CapabilityError):
        assert_spec_supported(
            engine, AgentSpec(name="a", engine="echo", output_schema="mypkg:M")
        )


def test_concrete_engine_without_capabilities_raises_at_import():
    """``Engine.__init_subclass__`` rejects a concrete engine that omits ``capabilities``."""
    from agentship.engines.base import Engine

    with pytest.raises(TypeError, match="capabilities"):

        class _NoCaps(Engine):
            name = "nocaps"

            def build(self, spec):
                return spec

            async def run(self, compiled, text, ctx):  # pragma: no cover
                raise NotImplementedError


def test_provider_gate_rejects_undeclared_provider():
    """langgraph declares no ``cohere`` provider, so a cohere model fails fast at build."""
    caps = EngineCapabilities(providers={"openai", "anthropic"})
    spec = AgentSpec(name="a", engine="langgraph", model="cohere/command-r")
    with pytest.raises(CapabilityError) as exc:
        caps.assert_supports_spec(spec)
    assert "cohere" in str(exc.value).lower()


def test_langgraph_build_rejects_undeclared_provider():
    """End-to-end: building a langgraph agent on cohere fails fast via the gate.

    Skipped if the langgraph engine package is not installed in this environment.
    """
    pytest.importorskip("agentship_langgraph")
    spec = AgentSpec(name="a", engine="langgraph", model="cohere/command-r")
    with pytest.raises(CapabilityError) as exc:
        build_agent(spec)
    assert "cohere" in str(exc.value).lower()


def test_provider_gate_allows_declared_provider():
    """A model on a declared provider builds without error."""
    caps = EngineCapabilities(providers={"openai", "anthropic"})
    spec = AgentSpec(name="a", engine="langgraph", model="openai/gpt-4o-mini")
    caps.assert_supports_spec(spec)  # no raise


def test_provider_gate_skipped_when_providers_empty():
    """Empty ``providers`` means unconstrained — any provider is allowed."""
    caps = EngineCapabilities()  # providers == set()
    spec = AgentSpec(name="a", engine="echo", model="cohere/command-r")
    caps.assert_supports_spec(spec)  # no raise


def test_provider_gate_ignores_model_without_prefix():
    """A model string with no ``provider/`` prefix carries no provider to gate."""
    caps = EngineCapabilities(providers={"openai"})
    spec = AgentSpec(name="a", engine="langgraph", model="gpt-4o-mini")
    caps.assert_supports_spec(spec)  # no raise


def test_unknown_engine_raises_engine_not_found():
    """A spec naming an unregistered engine raises EngineNotFoundError listing what's available."""
    with pytest.raises(EngineNotFoundError) as exc:
        build_agent(AgentSpec(name="a", engine="nope"))
    msg = str(exc.value)
    assert "nope" in msg
    assert "echo" in msg  # the message lists the available engines
