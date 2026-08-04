"""The middleware seam — the ordered, per-turn cross-cutting layer.

A :class:`Middleware` wraps the engine call: ``on_request`` runs before the engine
(and may mutate the :class:`~agentship.context.RunContext`), ``on_response`` runs
after (and may replace the :class:`~agentship.engines.base.Result`), and
``on_error`` observes a failure without swallowing it. Phase 0 ships the base class
with no-op defaults; concrete middlewares (memory, observability, guardrails)
arrive with their phases.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .context import RunContext
    from .engines.base import Result


class Middleware:
    """Base class for a cross-cutting stage. Override only the hooks you need.

    All three hooks default to a no-op so a middleware overriding one is
    unaffected by the others.
    """

    async def on_request(self, ctx: RunContext) -> None:
        """Prepare or validate the request before the engine runs. Default: no-op."""

    async def on_response(self, ctx: RunContext, result: Result) -> Result:
        """Post-process the result after the engine runs. Default: pass through."""
        return result

    async def on_error(self, ctx: RunContext, exc: BaseException) -> None:
        """Observe a failure raised by the engine (or a downstream hook).

        Runs before the exception propagates, in reverse pipeline order (LIFO,
        mirroring ``on_response``). This is an *observation* hook: returning a
        value or raising here does not swallow the original exception, which is
        always re-raised. Default: no-op.
        """
