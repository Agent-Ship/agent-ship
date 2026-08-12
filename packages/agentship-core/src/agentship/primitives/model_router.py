"""Choose which model a turn should use, before the engine runs.

A router takes an agent's spec and returns a model id string (e.g.
``"openai/gpt-4o-mini"``). The choice is pure and deterministic — no LLM call, no
I/O — so the same inputs always pick the same model. Routers are swappable by name
through the ``agentship.model_routers`` registry; :class:`DefaultModelRouter` is the
built-in and simply returns ``spec.model`` unchanged.

See DESIGN §13.5 for why routing is a separate step the engine never does itself.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from ..registry import Registry

if TYPE_CHECKING:
    from ..context import RunContext
    from ..spec import AgentSpec


class ModelRouter(ABC):
    """Base class for a model-choosing policy. Subclasses implement :meth:`pick`.

    ``pick`` must be pure — no network, randomness, or clock — so the same inputs
    always return the same model id.
    """

    @abstractmethod
    def pick(self, spec: AgentSpec, task_hint: str | None = None) -> str:
        """Return the model id to use for this turn (a LiteLLM string).

        ``task_hint`` is an optional coarse label (e.g. ``"classify"``) a richer
        policy may use to pick a cheaper model; the default router ignores it.
        """


class DefaultModelRouter(ModelRouter):
    """The built-in router: return ``spec.model`` unchanged, ignoring any hint.

    There is no model tiering yet; a later phase can add it behind this same class.
    """

    def pick(self, spec: AgentSpec, task_hint: str | None = None) -> str:
        """Return ``spec.model`` (or ``""`` when the spec sets none)."""
        return spec.model or ""


#: Registry of model routers, keyed by name. A plugin adds one via the
#: ``agentship.model_routers`` entry-point group; the built-in is registered below.
MODEL_ROUTERS: Registry[type[ModelRouter]] = Registry(
    "agentship.model_routers", label="model router"
)

#: The name the built-in router registers under, and the default when none is asked for.
DEFAULT_MODEL_ROUTER_NAME = "default"

MODEL_ROUTERS.register(DEFAULT_MODEL_ROUTER_NAME, DefaultModelRouter)


def resolve_model_router(name: str | None = None) -> ModelRouter:
    """Return a router instance by name, or the built-in default when ``name`` is ``None``.

    Raises :class:`~agentship.errors.CapabilityError` when a named router is not
    registered, listing the available names.
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
    """Choose this turn's model and store it on ``ctx.routed_model`` for the engine to read.

    Called by the runtime before the engine runs. No-op when ``ctx`` is ``None`` so
    helpers called outside a turn don't crash. See DESIGN §13.5 for why routing is a
    separate step the engine never does itself.
    """
    if ctx is None:
        return
    chosen = (router or resolve_model_router()).pick(spec)
    ctx.routed_model = chosen
