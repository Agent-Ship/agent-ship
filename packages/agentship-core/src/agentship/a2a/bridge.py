"""Bridge the authoring layer (``MemberSpec``) to a resolvable ``AgentRef`` (§C3 connective tissue).

The authoring-side blocks live in :mod:`agentship.spec` (so a spec parses with the interop layer
absent); the resolver consumes :class:`~agentship.a2a.models.AgentRef`. This module's
:func:`agent_ref_from_member` is the one-call translation, so a multi-agent builder treats a remote
member and an in-process one identically — mapping each to an ``AgentRef`` and letting the resolver
pick the transport. Kept here (a2a → spec is a one-way import) rather than in ``spec`` so the spec
module never depends on the optional interop package.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..errors import CapabilityError
from .models import A2AAuthConfig, AgentRef, RemoteSpec

if TYPE_CHECKING:
    from ..spec import MemberSpec


def agent_ref_from_member(member: MemberSpec) -> AgentRef:
    """Map one declared team ``member`` onto the ``AgentRef`` the resolver understands.

    A member with an ``a2a`` block becomes a **remote** ref (its URL/auth/timeout carried across); a
    referenced (``ref``) or inline (``prompt``) member becomes a **local** ref keyed by the member
    name — the registry key its sub-agent registers under. A member declaring no resolvable target
    is a :class:`CapabilityError`, matching the "declare exactly one way" spec rule.
    """
    if member.a2a is not None:
        auth = A2AAuthConfig(**member.a2a.auth.model_dump())
        remote = RemoteSpec(
            url=member.a2a.url,
            card_path=member.a2a.card_path,
            auth=auth,
            timeout_seconds=member.a2a.timeout_seconds,
        )
        return AgentRef(name=member.name, remote=remote)
    if member.ref is not None or member.prompt is not None:
        return AgentRef(name=member.name, local_ref=member.name)
    raise CapabilityError(
        f"member {member.name!r} declares no resolvable target — cannot resolve it as a "
        f"specialist; give it a ref, a prompt, or an a2a block"
    )
