"""The two specialist implementations behind one ``send`` port: local and remote (§C3).

A supervisor's ``kit.specialist(name).send(text)`` must read the same whether the specialist runs
in-process or across the network — that transparency is the phase's core invariant. Both
:class:`LocalSpecialist` and :class:`RemoteSpecialist` expose the same ``send`` coroutine; the
:class:`~agentship.a2a.resolver.SpecialistResolver` decides which one a given ``AgentRef`` becomes,
and the caller never branches on transport.
"""

from __future__ import annotations

from typing import Any, Protocol

from ..context import RunContext
from .client import RemoteA2aAgent


class Specialist(Protocol):
    """The transport-agnostic specialist port: run a turn on ``text`` and return the reply text.

    The run context is bound when the specialist is resolved, so a supervisor's call site is just
    ``kit.specialist(name).send(text)`` — identical for an in-process or a networked specialist.
    """

    async def send(self, text: str) -> str:  # noqa: D102 - protocol stub
        ...


class _LocalAgent(Protocol):
    """The in-process agent's run port (a built ``RunnableAgent`` satisfies this)."""

    async def run(self, text: str, *, user_id: str = ...) -> Any:  # noqa: D102 - protocol stub
        ...


class LocalSpecialist:
    """An in-process specialist: run the registered agent through its own ``run`` port."""

    def __init__(self, name: str, agent: _LocalAgent, ctx: RunContext) -> None:
        """Bind the registered ``name``, the resolved in-process ``agent``, and the run context."""
        self.name = name
        self._agent = agent
        self._ctx = ctx

    async def send(self, text: str) -> str:
        """Run the agent under the caller's user and return its output as text."""
        result = await self._agent.run(text, user_id=self._ctx.caller.user_id)
        output = getattr(result, "output", result)
        return output if isinstance(output, str) else str(output)


class RemoteSpecialist:
    """A networked specialist: delegate to the A2A client, preserving the ``send`` port."""

    def __init__(self, name: str, remote_agent: RemoteA2aAgent, ctx: RunContext) -> None:
        """Bind the registered ``name``, the A2A client, and the run context to propagate."""
        self.name = name
        self._remote = remote_agent
        self._ctx = ctx

    async def send(self, text: str) -> str:
        """Send the turn over A2A (carrying the bound context) and return the reply text."""
        return await self._remote.send(text, self._ctx)
