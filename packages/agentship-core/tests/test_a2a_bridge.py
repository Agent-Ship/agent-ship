"""The spec → ``AgentRef`` bridge: turn a declared member into a resolvable reference (P05 · C3).

This is the connective tissue between the authoring layer (``MemberSpec`` in ``agentship.spec``) and
the resolver: a multi-agent builder calls :func:`agent_ref_from_member` on each member and hands the
``AgentRef`` to a :class:`SpecialistResolver`, so a networked member and an in-process one flow
through the same seam.
"""

from __future__ import annotations

import pytest
from agentship.a2a.bridge import agent_ref_from_member
from agentship.errors import CapabilityError
from agentship.spec import MemberSpec


def test_in_process_member_becomes_a_local_ref() -> None:
    """A member with ``ref`` maps to a local :class:`AgentRef` naming that agent."""
    ref = agent_ref_from_member(MemberSpec(name="billing", ref="./billing.yaml"))
    assert not ref.is_remote()
    # The local_ref is the member name (the registry key the sub-agent registers under).
    assert ref.local_ref == "billing"


def test_inline_member_becomes_a_local_ref() -> None:
    """An inline member (prompt only) is also in-process, keyed by its name."""
    ref = agent_ref_from_member(MemberSpec(name="greeter", prompt="say hi"))
    assert not ref.is_remote()
    assert ref.local_ref == "greeter"


def test_networked_member_becomes_a_remote_ref() -> None:
    """A member with an ``a2a`` block maps to a remote :class:`AgentRef` carrying the URL + auth."""
    member = MemberSpec(
        name="radiology",
        a2a={
            "url": "https://rad.internal/a2a/radiology",
            "timeout_seconds": 30,
            "auth": {"type": "bearer", "token_env": "RAD_A2A_TOKEN"},
        },
    )
    ref = agent_ref_from_member(member)
    assert ref.is_remote()
    assert ref.remote.url == "https://rad.internal/a2a/radiology"
    assert ref.remote.timeout_seconds == 30
    assert ref.remote.auth.type == "bearer"
    assert ref.remote.auth.token_env == "RAD_A2A_TOKEN"


def test_bridge_rejects_a_member_with_no_resolvable_target() -> None:
    """A member that is neither referenced, inline, nor networked cannot be resolved."""
    with pytest.raises(CapabilityError, match="cannot resolve|no .*target"):
        agent_ref_from_member(MemberSpec(name="empty"))
