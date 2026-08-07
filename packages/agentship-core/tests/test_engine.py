"""Tests for the engine seam: the capability gate and unknown-engine handling."""

from __future__ import annotations

import pytest
from agentship.errors import CapabilityError, EngineNotFoundError
from agentship.runtime import build_agent
from agentship.spec import AgentSpec, MemberSpec


def test_streaming_on_echo_is_allowed():
    """echo declares streaming, so a streaming spec builds without error."""
    agent = build_agent(AgentSpec(name="a", engine="echo", streaming=True))
    assert agent.spec.streaming is True


def test_structured_output_on_echo_raises_capability_error():
    """echo does NOT declare structured_output — a spec asking for it fails fast at build."""
    with pytest.raises(CapabilityError) as exc:
        build_agent(AgentSpec(name="a", engine="echo", output="MyModel"))
    assert "structured output" in str(exc.value).lower()


def test_members_on_echo_raises_capability_error():
    """echo is not multi-agent — a spec declaring members fails fast rather than dropping them."""
    spec = AgentSpec(name="team", engine="echo", members=[MemberSpec(name="m1")])
    with pytest.raises(CapabilityError) as exc:
        build_agent(spec)
    assert "multi-agent" in str(exc.value).lower()


def test_unknown_engine_raises_engine_not_found():
    """A spec naming an unregistered engine raises EngineNotFoundError listing what's available."""
    with pytest.raises(EngineNotFoundError) as exc:
        build_agent(AgentSpec(name="a", engine="nope"))
    msg = str(exc.value)
    assert "nope" in msg
    assert "echo" in msg  # the message lists the available engines
