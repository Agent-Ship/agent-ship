"""In-process specialist dispatch: ``AgentRef`` and output normalization (Phase 02 · C7).

A supervisor delegates a turn to another agent by name. :class:`AgentRef` is a thin handle around
that agent: :meth:`AgentRef.resolve` looks it up in a registry (fail-fast if absent) and
:meth:`AgentRef.run` invokes the agent through its **public ``run`` port** — the same seam any
caller uses — so a specialist on any engine (LangGraph, ADK, …) works without shared graph state.
The output is normalized to a plain dict and wrapped as a :class:`SpecialistResult` the
:class:`~agentship.primitives.conflict_resolver.ConflictResolver` can merge.

Deliberately dependency-light: the registry and the agent are typed as small ``Protocol``\\s (just
``get`` and ``run``), not concrete classes — so dispatch composes with any agent/registry and stays
easy to test with plain fakes. Engine-neutral, so it lives in core beside ``SpecialistResult``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from ..errors import CapabilityError
from .conflict_resolver import SpecialistResult

if TYPE_CHECKING:  # only a type hint on run(); avoids a core import cycle at load
    from ..context import RunContext


class Specialist(Protocol):
    """The one thing dispatch needs of an agent: run a turn, return a ``Result``-like object."""

    async def run(self, text: str, *, user_id: str = ...) -> Any:  # noqa: D102 - protocol stub
        ...


class SpecialistRegistry(Protocol):
    """A name → agent lookup (a plain dict satisfies this)."""

    def get(self, name: str) -> Specialist | None:  # noqa: D102 - protocol stub
        ...


def _as_dict(output: Any) -> dict[str, Any]:
    """Normalize a specialist's output to a plain dict for :class:`SpecialistResult`.

    A Pydantic model is ``model_dump()``'d; a dict is passed through; any other value (a string,
    a number) is wrapped as ``{"output": value}`` so the result shape is always a dict.
    """
    if hasattr(output, "model_dump"):
        return output.model_dump()
    if isinstance(output, dict):
        return output
    return {"output": output}


class AgentRef:
    """A resolved handle to another agent, invoked in-process through its ``run`` port."""

    def __init__(self, name: str, agent: Specialist) -> None:
        """Bind the specialist's registered ``name`` and the resolved ``agent``."""
        self.name = name
        self.agent = agent

    @classmethod
    def resolve(cls, name: str, registry: SpecialistRegistry) -> AgentRef:
        """Look ``name`` up in ``registry``; raise :class:`CapabilityError` if it isn't there."""
        agent = registry.get(name)
        if agent is None:
            raise CapabilityError(
                f"no agent registered as {name!r} — cannot dispatch to it; register it or fix "
                f"the name"
            )
        return cls(name, agent)

    async def run(self, message: str, ctx: RunContext) -> SpecialistResult:
        """Run the specialist on ``message`` (under the caller's user) and wrap its output.

        The specialist runs its **own** turn — its own session/thread and, if durable, its own
        checkpoint — so the supervisor only ever sees the returned structured output, never the
        specialist's internal state. On success ``error`` is ``None``.
        """
        result = await self.agent.run(message, user_id=ctx.caller.user_id)
        output = _as_dict(result.output)
        return SpecialistResult(
            name=self.name,
            output=output,
            confidence=output.get("confidence"),
            error=None,
        )
