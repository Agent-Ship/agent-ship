"""Build an agent from a spec, then run or stream it.

:func:`build_agent` checks the engine can do what the spec asks, then compiles the
agent into a :class:`RunnableAgent`. Each turn runs the middleware pipeline —
``on_request`` in order, ``on_response`` in reverse — with the current run stored in
the ``current_run`` contextvar so tools can read the caller's identity. If anything
raises, each middleware's ``on_error`` runs (reverse order) and the original error is
re-raised, never hidden. The contextvar is always put back when the turn ends.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator, Sequence
from typing import TYPE_CHECKING, Any

from .context import Caller, RunContext, RunMode, current_run
from .engines.base import ENGINES, Event, Result, assert_spec_supported
from .errors import EngineNotFoundError, SpecError
from .observability import NoOpObserver, Observer, SpanKind, current_observer, semconv
from .observability.phi import hashed_user_id
from .primitives.model_router import stamp_routed_model
from .spec import AgentSpec, load_spec, resolve_code

if TYPE_CHECKING:
    from .engines.base import Engine
    from .middleware import Middleware

logger = logging.getLogger(__name__)


def _caller_for(caller: Caller | None, user_id: str) -> Caller:
    """Return the turn's caller: the authenticated ``caller`` if given, else a dev caller.

    The service passes a fully authenticated ``caller`` (tenant + scopes) so the turn is
    scoped to a real identity. When none is passed — a script or a dev/CLI call — ``user_id``
    is wrapped in a single-tenant :class:`Caller` (``tenant_id="default"``), so an
    un-plumbed project still runs. The authenticated caller always wins over ``user_id``.
    """
    return caller if caller is not None else Caller(user_id=user_id)


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
        observer: Observer | None = None,
    ) -> None:
        """Bind a validated spec, its engine, the compiled agent, any middleware, and an observer.

        ``observer`` is the tracing seam: every turn opens a root ``agent`` span through it that
        wraps the whole middleware pipeline and the engine call, so the span tree is owned here (not
        by any single engine) and is identical whichever engine runs. It defaults to
        :class:`~agentship.observability.NoOpObserver`, so an agent built without tracing behaves
        exactly as before — no ``if observer:`` guards anywhere.
        """
        self.spec = spec
        self.engine = engine
        self.compiled = compiled
        self.middlewares: tuple[Middleware, ...] = tuple(middlewares)
        self.observer: Observer = observer if observer is not None else NoOpObserver()

    def _root_attrs(self, ctx: RunContext) -> dict[str, Any]:
        """Build the opening attributes of the root ``agent`` span from the turn's context.

        These are the frozen identity keys (agent/session/run/tenant/user + mode) every root span
        carries, so a trace is attributable to a caller and a turn without reading the payload. The
        user id is stamped only as a salted hash (PHI gate, §4.6) — the raw id never reaches a span.
        """
        return {
            semconv.AS_AGENT_NAME: ctx.agent_name,
            semconv.AS_SESSION_ID: ctx.session_id,
            semconv.AS_RUN_ID: ctx.run_id,
            semconv.AS_TENANT_ID: ctx.tenant_id,
            semconv.AS_USER_ID: hashed_user_id(ctx.user_id),
            semconv.AS_RUN_MODE: ctx.mode.value,
        }

    def _make_context(
        self, text: str, *, caller: Caller, session_id: str | None, mode: RunMode
    ) -> RunContext:
        """Build a fresh :class:`RunContext` for one turn.

        ``caller`` is the identity the whole turn is scoped to — its
        ``(tenant_id, user_id)`` partitions memory and the vault, and its ``scopes``
        gate what the turn may do. ``session_id`` is caller-supplied and stable across
        a conversation's turns; it is only minted (``uuid4().hex``) when the caller
        passes none. ``run_id`` is always minted fresh, identifying this single turn.
        ``mode`` marks whether the turn is an invoke or a stream so engines branch off
        it, not an invented key.
        """
        return RunContext(
            caller=caller,
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
        caller: Caller | None = None,
        user_id: str = "anonymous",
        session_id: str | None = None,
        middlewares: Sequence[Middleware] = (),
    ) -> Result:
        """Run one turn and return a :class:`Result`.

        Pass ``caller`` to scope the turn to a full authenticated identity (tenant +
        scopes) — this is how the runtime service threads the request's caller through.
        When ``caller`` is omitted, ``user_id`` is wrapped in a single-tenant
        :class:`Caller` so a scriptless/dev call still works.

        Sets the request context, runs ``on_request`` hooks in order, calls the
        engine, then ``on_response`` hooks in reverse (LIFO onion). If the engine
        or a hook raises — including cancellation — each middleware's ``on_error``
        runs in reverse order for observation, then the original exception is
        re-raised unchanged. The contextvar is always reset in ``finally``.
        """
        ctx = self._make_context(
            text, caller=_caller_for(caller, user_id), session_id=session_id, mode=RunMode.INVOKE
        )
        pipeline = (*self.middlewares, *middlewares)
        token = current_run.set(ctx)
        # Expose this agent's observer to the engine so it can attach its span-emitting callback to
        # the same observer whose root ``agent`` span is open — the inner tree then nests under it.
        observer_token = current_observer.set(self.observer)
        try:
            # The ``route`` step: stamp the chosen model id on the context before the
            # engine runs, so the adapter reads it and never routes itself (§13.5).
            stamp_routed_model(self.spec, ctx)
            # Open the root ``agent`` span around the whole pipeline so every guard/
            # memory/engine span nests under it, and stamp the trace id on the context
            # so middleware, the engine, and the service (X-Trace-Id) can read it.
            with self.observer.span(
                semconv.SPAN_AGENT, SpanKind.AGENT, self._root_attrs(ctx)
            ) as root:
                ctx.trace_id = self.observer.current_trace_id()
                try:
                    for mw in pipeline:
                        await mw.on_request(ctx)
                    result = await self.engine.run(self.compiled, ctx.input_text, ctx)
                    for mw in reversed(pipeline):
                        result = await mw.on_response(ctx, result)
                except BaseException as exc:
                    # Catch BaseException (not just Exception) so cancellation/timeout is
                    # observable by on_error; it is re-raised (through the span, which marks
                    # it errored) below, never swallowed.
                    root.set_attribute(semconv.AS_STATUS, "error")
                    await _run_error_hooks(pipeline, ctx, exc)
                    raise
                root.set_attribute(semconv.AS_STATUS, "ok")
                return result
        finally:
            # ``run``'s set/reset share a frame, so reset is valid here; guard it
            # anyway so teardown can never mask the exception in flight.
            try:
                current_run.reset(token)
            except ValueError:  # pragma: no cover - defensive; set/reset share a frame
                current_run.set(None)  # type: ignore[arg-type]
            try:
                current_observer.reset(observer_token)
            except ValueError:  # pragma: no cover - defensive; set/reset share a frame
                current_observer.set(None)

    async def stream(
        self,
        text: str,
        *,
        caller: Caller | None = None,
        user_id: str = "anonymous",
        session_id: str | None = None,
    ) -> AsyncIterator[Event]:
        """Stream events for one turn (allowed only if the engine declares streaming).

        Like :meth:`run`, but yields events instead of returning a result. Pass
        ``caller`` to scope the stream to a full authenticated identity (tenant +
        scopes); omit it and ``user_id`` is wrapped in a single-tenant caller. Sets the
        current run for this turn and always puts back the previous one when the
        stream ends — cleanly, on error, or when the caller stops early. We use
        ``set`` (not ``reset``) to restore, so cleanup never crashes even when an
        abandoned stream is torn down in a different context. See ``get_run_context``.
        """
        ctx = self._make_context(
            text, caller=_caller_for(caller, user_id), session_id=session_id, mode=RunMode.STREAM
        )
        previous = current_run.get(None)  # whatever run (if any) was active before this
        current_run.set(ctx)
        previous_observer = current_observer.get()  # restore, not reset — stream teardown is loose
        current_observer.set(self.observer)
        try:
            stamp_routed_model(self.spec, ctx)
            # The root span spans the whole generator — opened here, ended when the
            # ``with`` exits (clean finish, mid-stream error, or early disconnect).
            with self.observer.span(
                semconv.SPAN_AGENT, SpanKind.AGENT, self._root_attrs(ctx)
            ) as root:
                ctx.trace_id = self.observer.current_trace_id()
                try:
                    for mw in self.middlewares:
                        await mw.on_request(ctx)
                    async for event in self.engine.stream(self.compiled, ctx.input_text, ctx):
                        yield event
                except GeneratorExit:
                    # The caller stopped early (disconnect / break / GC). Not a failure, so
                    # on_error is not fired and the span stays "ok"; context is still restored.
                    raise
                except BaseException as exc:
                    root.set_attribute(semconv.AS_STATUS, "error")
                    await _run_error_hooks(self.middlewares, ctx, exc)
                    raise
                root.set_attribute(semconv.AS_STATUS, "ok")
        finally:
            current_run.set(previous)  # put the caller's previous run back; never raises
            current_observer.set(previous_observer)


def _resolve_code_spec(spec: AgentSpec) -> tuple[AgentSpec, Any]:
    """Author an agent in Python: call the ``code:`` builder, return (spec, authored).

    Resolves the ``"module:function"`` reference on ``spec.code`` and invokes it.
    The builder may return one of two things (declare, don't fake — never a silent
    no-op):

    - an :class:`AgentSpec` — a purely declarative Python-authored spec. The
      returned spec must not itself re-set ``code:`` (that would loop), and the
      *authored* half of the tuple is ``None`` (there is no custom agent object).
    - an **authored agent** — any object carrying a ``.spec`` attribute that is an
      :class:`AgentSpec` (e.g. a ``LangGraphAgent`` subclass instance). This is the
      custom-authoring path: the effective spec is ``authored.spec`` and the object
      itself is threaded to the engine's ``build`` so an engine that supports custom
      authoring can call into it. The core stays vendor-neutral — it only reads
      ``.spec`` and forwards the object opaquely.

    Raises :class:`~agentship.errors.SpecError` when the builder returns neither
    shape, or returns a spec that itself sets ``code:``.
    """
    builder = resolve_code(spec.code)  # type: ignore[arg-type]  # guarded by caller
    built = builder()
    if isinstance(built, AgentSpec):
        effective, authored = built, None
    elif isinstance(getattr(built, "spec", None), AgentSpec):
        effective, authored = built.spec, built
    else:
        raise SpecError(
            f"code {spec.code!r} must return an AgentSpec or an authored agent with "
            f"a `.spec` AgentSpec, got {type(built).__name__}"
        )
    if effective.code is not None:
        raise SpecError(
            f"code {spec.code!r} returned a spec that itself sets code: "
            f"{effective.code!r} — a Python builder must return a concrete spec"
        )
    return effective, authored


def build_agent(
    spec: AgentSpec | str,
    *,
    middlewares: Sequence[Middleware] = (),
    observer: Observer | None = None,
) -> RunnableAgent:
    """Build a :class:`RunnableAgent` from an :class:`AgentSpec` or a YAML path.

    When the spec sets ``code: "module:function"``, the agent is authored in Python:
    that builder is resolved and called. It may return a plain :class:`AgentSpec`
    (a declarative Python-authored spec) or an **authored agent** carrying its own
    ``.spec`` (the custom-authoring path — e.g. a ``LangGraphAgent`` subclass whose
    ``build_graph`` the engine will call). Either way the effective spec is honoured
    and the authored object (if any) is threaded to the engine's ``build`` — never
    silently ignored (declare, don't fake). Otherwise the spec is used as-is. Then
    the engine is resolved by name, the spec is capability-validated against it
    (failing fast on an unsupported request), and the agent is compiled.

    Raises :class:`~agentship.errors.SpecError` when a ``code:`` builder does not
    return an :class:`AgentSpec`, :class:`~agentship.errors.EngineNotFoundError` when
    the engine is unknown, or :class:`~agentship.errors.CapabilityError` when the spec
    asks for something the engine cannot do.
    """
    if isinstance(spec, str):
        spec = load_spec(spec)
    authored: Any = None
    if spec.code is not None:
        spec, authored = _resolve_code_spec(spec)
    engine_cls = ENGINES.get(spec.engine)
    if engine_cls is None:
        raise EngineNotFoundError(
            f"engine {spec.engine!r} is not registered — available: {ENGINES.names()} "
            f"(install its package, or register it)"
        )
    engine = engine_cls()
    assert_spec_supported(engine, spec)
    # Only thread ``authored`` when a custom Python-authored agent was produced, so
    # an engine that never implements custom authoring keeps the plain, two-arg
    # ``build(spec)`` contract and needs no change to opt out of the seam.
    compiled = engine.build(spec, authored) if authored is not None else engine.build(spec)
    return RunnableAgent(spec, engine, compiled, middlewares=middlewares, observer=observer)
