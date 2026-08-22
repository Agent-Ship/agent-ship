"""Discover observability providers by name and build an :class:`Observer` from a spec block.

Providers register under the ``agentship.observers`` entry-point group — the same extension
mechanism engines and auth use — so the OpenTelemetry implementation (and any vendor's) is
discovered without a kernel edit. Each entry point is a *factory*: a callable taking an agent's
:class:`~agentship.spec.ObservabilitySpec` and returning a configured
:class:`~agentship.observability.Observer`.

:func:`resolve_observer` turns an agent's ``observability`` block into an observer — or ``None``
when tracing is off — and is the seam :func:`~agentship.runtime.build_agent` calls, so a
declarative ``observability:`` block actually attaches a tracer to a real run instead of the whole
pipeline sitting unreachable behind a manual ``build_observer`` call.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from ..errors import CapabilityError
from ..registry import Registry
from .observer import Observer

if TYPE_CHECKING:  # only for typing — the kernel never needs the class at runtime here
    from ..spec import ObservabilitySpec

#: A factory that builds a configured observer from an agent's observability block.
ObserverFactory = Callable[["ObservabilitySpec"], Observer]

#: The shared registry of observer factories, discovered via the entry-point group. The built-in
#: ``otel`` provider is contributed by ``agentship-observability`` (installed via the
#: ``observability`` extra); the kernel never imports it directly, keeping itself vendor-free.
OBSERVERS: Registry[ObserverFactory] = Registry("agentship.observers", label="observer")


def resolve_observer(observability: ObservabilitySpec | None) -> Observer | None:
    """Build the observer an agent's ``observability`` block asks for, or ``None`` when off.

    Returns ``None`` — meaning "leave the agent on the no-op observer" — when there is no block or
    its ``provider`` is ``none``. Otherwise it resolves the provider by name through
    :data:`OBSERVERS` and calls its factory with the block. A provider named but not installed is a
    fail-fast :class:`~agentship.errors.CapabilityError` (listing what is installed) rather than a
    silently untraced run — the operator asked for tracing and should hear that the adapter is
    missing.
    """
    if observability is None or observability.provider == "none":
        return None
    factory = OBSERVERS.get(observability.provider)
    if factory is None:
        raise CapabilityError(
            f"observability provider {observability.provider!r} is not installed — available: "
            f"{OBSERVERS.names()} (install the adapter, e.g. `pip install agentship[observability]`)"  # noqa: E501
        )
    return factory(observability)


__all__ = ["OBSERVERS", "ObserverFactory", "resolve_observer"]
