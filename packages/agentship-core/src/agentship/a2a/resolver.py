"""Resolve one ``AgentRef`` into a specialist — in-process or networked (§C3).

:class:`SpecialistResolver` is the single seam where a specialist reference becomes a callable. A
``local_ref`` is looked up in the registry and wrapped as a :class:`LocalSpecialist`; a ``remote``
block becomes a :class:`RemoteSpecialist` over an A2A client. Either way the caller gets the same
``send`` port, so a supervisor graph never branches on transport (the transparency invariant).
"""

from __future__ import annotations

from ..context import RunContext
from ..errors import CapabilityError
from .client import HttpxTransport, RemoteA2aAgent
from .models import AgentRef
from .specialist import LocalSpecialist, RemoteSpecialist, Specialist


class SpecialistResolver:
    """Turn an :class:`AgentRef` into a :class:`Specialist`, picking transport from the ref."""

    def __init__(self, *, registry, transport=None) -> None:
        """Bind the in-process ``registry`` (name → agent) and the A2A ``transport`` for remotes.

        ``registry`` is any ``name → agent`` mapping (a plain dict works). ``transport`` is the A2A
        transport used for networked refs; it defaults to :class:`HttpxTransport` so production
        needs no wiring, while tests inject a fake to exercise the client without a network.
        """
        self._registry = registry
        self._transport = transport if transport is not None else HttpxTransport()

    def resolve(self, ref: AgentRef, ctx: RunContext) -> Specialist:
        """Resolve ``ref`` to a specialist; fail fast on an unknown local ref.

        A remote ref is bound to the A2A client here but not called until ``send`` — resolution is
        cheap and synchronous; the network work happens on the turn.
        """
        if ref.is_remote():
            remote_agent = RemoteA2aAgent(ref.remote, transport=self._transport)
            return RemoteSpecialist(ref.name, remote_agent, ctx)
        agent = self._registry.get(ref.local_ref)
        if agent is None:
            raise CapabilityError(
                f"no agent registered as {ref.local_ref!r} — cannot resolve specialist "
                f"{ref.name!r}; register it or fix the ref"
            )
        return LocalSpecialist(ref.name, agent, ctx)
