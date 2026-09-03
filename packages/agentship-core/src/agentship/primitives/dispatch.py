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

import asyncio
from typing import TYPE_CHECKING, Any, Literal, Protocol

from ..errors import CapabilityError
from .conflict_resolver import SpecialistResult

#: How a set of specialists is invoked for one turn (§4 C7).
Strategy = Literal["single", "parallel", "sequential"]

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


def _error_result(ref: AgentRef, message: str) -> SpecialistResult:
    """A dropped-from-merge result for a specialist that failed or timed out."""
    return SpecialistResult(name=ref.name, output={}, confidence=None, error=message)


async def _run_safely(
    ref: AgentRef, message: str, ctx: RunContext, timeout_s: float | None
) -> SpecialistResult:
    """Run one specialist, turning any failure or timeout into an ``error`` result, never a crash.

    A specialist that raises (after its own retries) or overruns ``timeout_s`` must not take down
    the whole fan-out — its contribution becomes a :class:`SpecialistResult` with ``error`` set,
    which the resolver drops from the merge (partial-merge, DESIGN §10). A timed-out specialist is
    retryable by the bounded-retry loop (C6).
    """
    try:
        if timeout_s is not None:
            return await asyncio.wait_for(ref.run(message, ctx), timeout_s)
        return await ref.run(message, ctx)
    except TimeoutError:
        return _error_result(ref, f"timed out after {timeout_s}s")
    except Exception as exc:  # noqa: BLE001 - deliberately broad: any specialist failure degrades
        return _error_result(ref, str(exc))


def _forward_text(result: SpecialistResult) -> str:
    """The text a ``sequential`` step feeds to the next specialist: the prior output as a string."""
    output = result["output"]
    value = output.get("output", output)
    return value if isinstance(value, str) else str(value)


#: The dispatch strategies a supervisor may ask for. Named once so the guard that rejects an
#: unknown one and the branches that implement them cannot drift apart.
STRATEGIES = frozenset({"single", "parallel", "sequential"})


async def dispatch(
    strategy: Strategy,
    refs: list[AgentRef],
    message: str,
    ctx: RunContext,
    *,
    timeout_s: float | None = None,
) -> list[SpecialistResult]:
    """Invoke ``refs`` for one turn under ``strategy``, returning a result per specialist.

    - ``single`` — run just ``refs[0]``.
    - ``parallel`` — run every ref concurrently with the same ``message`` (order preserved).
    - ``sequential`` — run refs in order, feeding each one's output text to the next.

    ``timeout_s`` caps each specialist's wall-clock; an overrun becomes an ``error`` result. Every
    specialist is run through :func:`_run_safely`, so a failure or timeout is an ``error`` result,
    never a crash. An unknown ``strategy`` fails fast with :class:`CapabilityError`.
    """
    if strategy not in STRATEGIES:
        raise CapabilityError(
            f"unknown dispatch strategy {strategy!r} — use 'single', 'parallel', or 'sequential'"
        )
    if not refs:
        # Nothing to dispatch is a legitimate state, not an error: the dispatch node computes
        # its specialists from the retry helper on a later pass, and that list is empty when
        # nothing is retryable. Returning no results lets the resolver merge nothing and the
        # turn finish; `refs[0]` used to raise IndexError and took the whole run down.
        #
        # Checked AFTER the strategy, so an empty list cannot mask a misconfigured one.
        return []
    if strategy == "single":
        return [await _run_safely(refs[0], message, ctx, timeout_s)]
    if strategy == "parallel":
        return list(await asyncio.gather(*(_run_safely(r, message, ctx, timeout_s) for r in refs)))
    if strategy == "sequential":
        results: list[SpecialistResult] = []
        text = message
        for ref in refs:
            result = await _run_safely(ref, text, ctx, timeout_s)
            results.append(result)
            text = _forward_text(result)
        return results
    # pragma: no cover - unreachable: STRATEGIES was checked above
    raise AssertionError(f"unreachable strategy {strategy!r}")
