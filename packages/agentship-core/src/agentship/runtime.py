"""The runtime glue: build an agent from a spec, then run/stream it.

:func:`build_agent` resolves + capability-validates the engine and compiles the
agent into a :class:`RunnableAgent`. ``run``/``stream`` drive the engine inside the
``current_run`` contextvar and the middleware pipeline (an LIFO "onion":
``on_request`` in order, ``on_response`` in reverse). On failure, ``on_error`` fires
in reverse order for observation and the *original* exception is re-raised — never
masked. The contextvar is reset on every path, including an abandoned stream.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator, Sequence
from typing import TYPE_CHECKING, Any

from .context import Principal, RunContext, RunMode, current_run
from .engines.base import ENGINES, Event, Result, assert_spec_supported
from .errors import EngineNotFoundError
from .spec import AgentSpec, load_spec

if TYPE_CHECKING:
    from .engines.base import Engine
    from .middleware import Middleware

logger = logging.getLogger(__name__)

_UNSET = object()


async def _run_error_hooks(
    pipeline: Sequence[Middleware], ctx: RunContext, exc: BaseException
) -> None:
    """Run every middleware's ``on_error`` in reverse order, isolating failures.

    ``on_error`` is an observation hook: it must never mask the original exception,
    and one misbehaving observer must not starve the rest. Each call is wrapped so
    an observer that raises is logged and swallowed and the loop continues. The
    caller re-raises the original ``exc`` after this returns; this helper never
    raises.
    """
    for mw in reversed(tuple(pipeline)):
        try:
            await mw.on_error(ctx, exc)
        except BaseException as observer_exc:  # noqa: BLE001 - must not mask the real one
            logger.warning(
                "middleware %s.on_error raised while observing %r (swallowed to "
                "preserve the original exception)",
                type(mw).__name__,
                exc,
                exc_info=observer_exc,
            )


class _StreamRunContextScope:
    """Same-frame contextvar management for the streaming generator.

    A plain ``token = set(ctx)`` / ``finally: reset(token)`` leaks for an
    *abandoned* async generator: the first ``__anext__`` sets the contextvar in
    the caller's context, but the generator's ``finally`` runs later in the async-
    generator finalizer's *own* context (under ``GeneratorExit`` from an SSE
    disconnect / ``break`` / GC), where the token is invalid and the caller's
    context is unreachable — so ``reset`` raises ``ValueError`` ("Token created in
    a different Context") and the caller's ``current_run`` stays pointing at the
    finished run.

    This scope sidesteps that by only touching the contextvar at the ``yield``
    boundary the caller observes: it snapshots the previous value, ``arm()``s (sets
    ``ctx``) while the engine produces the next event, and ``restore()``s the
    previous value right before control returns to the caller. Every operation
    swallows errors so teardown never raises.
    """

    def __init__(self, ctx: RunContext) -> None:
        """Snapshot the current ``current_run`` value and remember the run's ctx."""
        self._ctx = ctx
        self._previous = current_run.get(_UNSET)

    def arm(self) -> None:
        """Set ``current_run`` to this run's ctx (for the engine's next step)."""
        try:
            current_run.set(self._ctx)
        except Exception:  # noqa: BLE001 - never raise from context management
            pass

    def restore(self) -> None:
        """Restore ``current_run`` to the value it held before this run began.

        Uses ``set`` (not ``reset``), so it is safe in whatever context this runs
        in — including the foreign-context generator finalizer. When there was no
        previous value the variable is cleared to ``None``.
        """
        try:
            if self._previous is _UNSET:
                current_run.set(None)  # type: ignore[arg-type]
            else:
                current_run.set(self._previous)
        except Exception:  # noqa: BLE001 - teardown must never raise
            pass


class RunnableAgent:
    """A built agent, ready to serve turns.

    Construct via :func:`build_agent`. Holds the compiled artifact and the engine;
    all per-turn state flows through :class:`RunContext`, so one instance serves
    concurrent turns safely.
    """

    def __init__(
        self,
        spec: AgentSpec,
        engine: Engine,
        compiled: Any,
        middlewares: Sequence[Middleware] = (),
    ) -> None:
        """Bind a validated spec, its engine, the compiled agent, and any middleware."""
        self.spec = spec
        self.engine = engine
        self.compiled = compiled
        self.middlewares: tuple[Middleware, ...] = tuple(middlewares)

    def _make_context(
        self, text: str, *, user_id: str, session_id: str | None, mode: RunMode
    ) -> RunContext:
        """Build a fresh :class:`RunContext` for one turn.

        Wraps ``user_id`` in a single-tenant :class:`Principal` (``tenant_id`` falls
        back to ``"default"``), so a project with no auth just works. ``session_id``
        is caller-supplied and stable across a conversation's turns; it is only
        minted (``uuid4().hex``) when the caller passes none. ``run_id`` is always
        minted fresh, identifying this single turn. ``mode`` marks whether the turn
        is an invoke or a stream so engines branch off it, not an invented key.
        """
        return RunContext(
            principal=Principal(user_id=user_id),
            session_id=session_id if session_id is not None else uuid.uuid4().hex,
            run_id=uuid.uuid4().hex,
            agent_name=self.spec.name,
            mode=mode,
            input_text=text,
        )

    async def run(
        self,
        text: str,
        *,
        user_id: str = "anonymous",
        session_id: str | None = None,
        middlewares: Sequence[Middleware] = (),
    ) -> Result:
        """Run one turn and return a :class:`Result`.

        Sets the request context, runs ``on_request`` hooks in order, calls the
        engine, then ``on_response`` hooks in reverse (LIFO onion). If the engine
        or a hook raises — including cancellation — each middleware's ``on_error``
        runs in reverse order for observation, then the original exception is
        re-raised unchanged. The contextvar is always reset in ``finally``.
        """
        ctx = self._make_context(
            text, user_id=user_id, session_id=session_id, mode=RunMode.INVOKE
        )
        pipeline = (*self.middlewares, *middlewares)
        token = current_run.set(ctx)
        try:
            for mw in pipeline:
                await mw.on_request(ctx)
            result = await self.engine.run(self.compiled, ctx.input_text, ctx)
            for mw in reversed(pipeline):
                result = await mw.on_response(ctx, result)
            return result
        except BaseException as exc:
            # Catch BaseException (not just Exception) so cancellation/timeout is
            # observable by on_error; it is then re-raised below, never swallowed.
            await _run_error_hooks(pipeline, ctx, exc)
            raise
        finally:
            # ``run``'s set/reset share a frame, so reset is valid here; guard it
            # anyway so teardown can never mask the exception in flight.
            try:
                current_run.reset(token)
            except ValueError:  # pragma: no cover - defensive; set/reset share a frame
                current_run.set(None)  # type: ignore[arg-type]

    async def stream(
        self,
        text: str,
        *,
        user_id: str = "anonymous",
        session_id: str | None = None,
    ) -> AsyncIterator[Event]:
        """Stream events for one turn (capability-gated on the engine's ``streaming``).

        Mirrors :meth:`run`: a fresh ``run_id`` per call and ``on_error`` observation
        on failure before the exception propagates. Robust teardown: an SSE
        disconnect, an early ``break``, or GC of the abandoned generator closes it
        with :class:`GeneratorExit` whose ``finally`` runs in the finalizer's own
        context — where a plain ``reset(token)`` both raises and cannot undo the
        leak. So the contextvar is managed by :class:`_StreamRunContextScope`, which
        only touches ``current_run`` at the ``yield`` boundary; the caller never
        observes a leak whether the stream completes or is abandoned.
        """
        ctx = self._make_context(
            text, user_id=user_id, session_id=session_id, mode=RunMode.STREAM
        )
        scope = _StreamRunContextScope(ctx)
        scope.arm()
        try:
            for mw in self.middlewares:
                await mw.on_request(ctx)
            async for event in self.engine.stream(self.compiled, ctx.input_text, ctx):
                # Restore the caller's context *before* handing them the event, so
                # they never observe ``current_run`` even if they break/drop here.
                scope.restore()
                yield event
                # Re-arm for the engine's next step now that control is back.
                scope.arm()
        except GeneratorExit:
            # The consumer dropped the generator (disconnect / break / GC). Not a
            # run failure, so on_error is intentionally NOT fired; ``finally``
            # restores the caller's context.
            raise
        except BaseException as exc:
            # Real failure (including cancellation): observe, then re-raise unchanged.
            await _run_error_hooks(self.middlewares, ctx, exc)
            raise
        finally:
            # Restore the caller's previous ``current_run``; safe in any context
            # (uses ``set``, not ``reset``) and never raises.
            scope.restore()


def build_agent(spec: AgentSpec | str, *, middlewares: Sequence[Middleware] = ()) -> RunnableAgent:
    """Build a :class:`RunnableAgent` from an :class:`AgentSpec` or a YAML path.

    Resolves the engine by name, capability-validates the spec against it (failing
    fast on an unsupported request), then compiles the agent. Raises
    :class:`~agentship.errors.EngineNotFoundError` when the engine is unknown, or
    :class:`~agentship.errors.CapabilityError` when the spec asks for something the
    engine cannot do.
    """
    if isinstance(spec, str):
        spec = load_spec(spec)
    engine_cls = ENGINES.get(spec.engine)
    if engine_cls is None:
        raise EngineNotFoundError(
            f"engine {spec.engine!r} is not registered — available: {ENGINES.names()} "
            f"(install its package, or register it)"
        )
    engine = engine_cls()
    assert_spec_supported(engine, spec)
    compiled = engine.build(spec)
    return RunnableAgent(spec, engine, compiled, middlewares=middlewares)
