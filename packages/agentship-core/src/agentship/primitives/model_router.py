"""The ``ModelRouter`` primitive: choose which model id a turn should use.

Routing is a **pure, deterministic, request-time** decision (DESIGN §13.5): given a
spec (and an optional hint about the task) it returns a model id string. It is
*never* an LLM call and does no I/O, so the same inputs always yield the same
choice. The kernel keeps routing here, in the ``route`` step, and out of every
engine adapter: the runtime calls :func:`stamp_routed_model` before the engine runs,
writing the chosen id onto :attr:`~agentship.context.RunContext.routed_model`; the
adapter then *reads* that value and never routes itself (§13.5 purity).

:class:`DefaultModelRouter` is the v0.1 policy — a deterministic pass-through that
returns ``spec.model``. Richer tiering (cheap/long-context variants) is a later
phase; it slots in behind the same :meth:`ModelRouter.pick` signature with no change
to the runtime or the adapters. Routers are swappable: a distribution registers one
under the ``agentship.model_routers`` entry-point group and :func:`resolve_model_router`
returns it by name (the built-in default when none is asked for).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from ..registry import Registry

if TYPE_CHECKING:
    from ..context import RunContext
    from ..spec import AgentSpec


class ModelRouter(ABC):
    """Chooses the model id for a turn — deterministic, never an LLM (DESIGN §13.5).

    A concrete router implements :meth:`pick`. The contract is strict on purpose:
    ``pick`` must be pure (no network, no randomness, no clock) so the choice is
    reproducible and cheap, and it must return a plain LiteLLM model id string the
    adapter can resolve. The ``route`` step calls it once per turn; the engine never
    does.
    """

    @abstractmethod
    def pick(self, spec: AgentSpec, task_hint: str | None = None) -> str:
        """Return the model id to use for this turn — deterministically.

        ``spec`` is the agent being run; ``task_hint`` is an optional, coarse label
        (e.g. ``"classify"``) a later policy may use to pick a cheaper variant. The
        return value is a LiteLLM model id string (e.g. ``"openai/gpt-4o-mini"``).
        Implementations must not call an LLM or do any I/O.
        """


class DefaultModelRouter(ModelRouter):
    """The v0.1 router: a deterministic pass-through returning ``spec.model``.

    There is no tiering yet — the chosen model is exactly the one the spec declares,
    every time, for every hint. This is the honest default (declare, don't fake): it
    makes the routing *seam* real (the ``route`` step stamps a value the adapter
    reads) without pretending to a smarter policy than v0.1 ships. Cheap/long-context
    tiering lands in a later phase behind this same signature.
    """

    def pick(self, spec: AgentSpec, task_hint: str | None = None) -> str:
        """Return ``spec.model`` unchanged (or ``""`` when the spec sets none)."""
        return spec.model or ""


#: The registry of model routers, discovered via the ``agentship.model_routers``
#: entry-point group. A distribution ships a :class:`ModelRouter` subclass plus one
#: entry-point line and it becomes selectable by name; the built-in default is
#: registered in-code below so it resolves with no plugin installed.
MODEL_ROUTERS: Registry[type[ModelRouter]] = Registry(
    "agentship.model_routers", label="model router"
)

#: The name the built-in :class:`DefaultModelRouter` registers under; also the
#: name :func:`resolve_model_router` resolves when the caller asks for none.
DEFAULT_MODEL_ROUTER_NAME = "default"

MODEL_ROUTERS.register(DEFAULT_MODEL_ROUTER_NAME, DefaultModelRouter)


def resolve_model_router(name: str | None = None) -> ModelRouter:
    """Return an instantiated :class:`ModelRouter` by name (the default when none).

    With ``name=None`` this returns the built-in :class:`DefaultModelRouter`, so the
    harness routes with zero configuration. A ``name`` selects a router registered
    under the ``agentship.model_routers`` entry-point group (or in-code on
    :data:`MODEL_ROUTERS`), so a project can swap in its own policy without touching
    the runtime. Raises :class:`~agentship.errors.CapabilityError` when a named router
    is not registered, listing what is available so the fix is obvious.
    """
    from ..errors import CapabilityError

    lookup = name or DEFAULT_MODEL_ROUTER_NAME
    router_cls = MODEL_ROUTERS.get(lookup)
    if router_cls is None:
        raise CapabilityError(
            f"model router {lookup!r} is not registered — available: "
            f"{MODEL_ROUTERS.names()} (install its package, or register it)"
        )
    return router_cls()


def stamp_routed_model(
    spec: AgentSpec, ctx: RunContext | None, *, router: ModelRouter | None = None
) -> None:
    """Run the ``route`` step: stamp the chosen model id onto ``ctx.routed_model``.

    Called by the runtime before the engine runs. It resolves the router (the
    default unless one is passed), asks it to :meth:`~ModelRouter.pick` a model id
    for ``spec``, and writes that id onto :attr:`RunContext.routed_model` — the value
    the engine adapter reads (§13.5). When ``ctx`` is ``None`` (a helper invoked
    outside a turn) it is a safe no-op rather than an error, so nothing crashes off
    the run path.
    """
    if ctx is None:
        return
    chosen = (router or resolve_model_router()).pick(spec)
    ctx.routed_model = chosen
