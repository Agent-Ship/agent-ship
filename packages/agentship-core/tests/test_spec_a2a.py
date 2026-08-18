"""Parsing the authoring-side A2A blocks: networked members and ``a2a.expose`` (P05 · C3/C4).

These fields live in :mod:`agentship.spec` (not the optional ``agentship.a2a`` subtree) precisely
so a spec still loads when the whole interop layer is absent — the ``interop.optional`` invariant.
"""

from __future__ import annotations

import pytest
from agentship.errors import SpecError
from agentship.spec import AgentSpec, MemberSpec


def test_member_can_be_networked_over_a2a() -> None:
    """A member with an ``a2a`` block is a networked specialist (url captured, defaults filled)."""
    member = MemberSpec(
        name="radiology",
        a2a={"url": "https://rad.internal/a2a/radiology", "auth": {"type": "oauth2"}},
    )
    assert member.a2a is not None
    assert member.a2a.url == "https://rad.internal/a2a/radiology"
    assert member.a2a.card_path == "/.well-known/agent-card.json"
    assert member.a2a.auth.type == "oauth2"
    assert member.a2a.timeout_seconds == 60


def test_member_a2a_excludes_inline_prompt_and_ref() -> None:
    """A networked member cannot also be an inline or referenced in-process one (exactly one)."""
    with pytest.raises(SpecError, match="exactly one|networked"):
        MemberSpec(name="x", prompt="hi", a2a={"url": "https://z/a2a/z"})
    with pytest.raises(SpecError, match="exactly one|networked"):
        MemberSpec(name="x", ref="./x.yaml", a2a={"url": "https://z/a2a/z"})


def test_agent_can_declare_a2a_exposure() -> None:
    """An ``a2a.expose`` block turns on serving the agent over A2A with named schemes."""
    spec = AgentSpec(name="triage", engine="echo", a2a={"expose": True, "security": ["oauth2"]})
    assert spec.a2a is not None
    assert spec.a2a.expose is True
    assert spec.a2a.security == ["oauth2"]


def test_a2a_absent_means_not_exposed() -> None:
    """No ``a2a`` block ⇒ the agent is not exposed (Layer 0 agents never hit the network)."""
    spec = AgentSpec(name="triage", engine="echo")
    assert spec.a2a is None


def test_exposed_agent_requires_a_security_scheme() -> None:
    """Default-deny: ``expose: true`` with no ``security`` is rejected at load (§C6)."""
    with pytest.raises(SpecError, match="security"):
        AgentSpec(name="triage", engine="echo", a2a={"expose": True})
