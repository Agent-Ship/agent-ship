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

import os
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


#: Env var naming the backends every agent's spans ship to, comma-separated (e.g.
#: ``opik`` or ``opik,langsmith``). It lives in the environment, not in each agent's YAML,
#: because WHERE traces go is a property of the deployment: the same agent ships to Opik in
#: production, to console on a laptop, and nowhere in an offline test run. Hardcoding a backend
#: per agent also makes a keyless suite dial out to it.
EXPORTERS_ENV = "AGENTSHIP_OTEL_EXPORTERS"


def default_exporters() -> list[str]:
    """Backends named by :data:`EXPORTERS_ENV`, or none.

    Empty by default on purpose: the span tree is still built and can be asserted on
    in-process, but nothing is shipped, so an offline run never reaches for a network backend.
    """
    raw = os.getenv(EXPORTERS_ENV, "")
    return [name.strip() for name in raw.split(",") if name.strip()]


#: Env var allowing exporters that ship spans off-box, deployment-wide. The per-agent
#: ``allow_saas_exporter`` flag stops one agent's YAML from quietly exfiltrating; this is its
#: operator-level counterpart, so pointing every agent at a hosted backend is one deliberate
#: decision in one place rather than an edit to every spec file. An explicit ``false`` in a
#: spec still wins.
ALLOW_SAAS_ENV = "AGENTSHIP_OTEL_ALLOW_SAAS"


def saas_egress_allowed() -> bool:
    """Whether the deployment has consented to off-box exporters. Off unless explicitly set."""
    return os.getenv(ALLOW_SAAS_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def tracing_is_on(observability: ObservabilitySpec | None) -> bool:
    """Whether this agent is traced. Absent block means YES — tracing is on by default.

    An agent that emitted nothing unless its YAML opted in was the reason most agents were
    invisible, and the reason a supervisor's members produced no spans at all. ``provider:
    none`` remains the explicit off switch.
    """
    return observability is None or observability.provider != "none"


def resolve_observer(observability: ObservabilitySpec | None) -> Observer | None:
    """Build the observer an agent's ``observability`` block asks for, or ``None`` when off.

    Returns ``None`` — meaning "leave the agent on the no-op observer" — when there is no block or
    its ``provider`` is ``none``. Otherwise it resolves the provider by name through
    :data:`OBSERVERS` and calls its factory with the block. A provider named but not installed is a
    fail-fast :class:`~agentship.errors.CapabilityError` (listing what is installed) rather than a
    silently untraced run — the operator asked for tracing and should hear that the adapter is
    missing.
    """
    if not tracing_is_on(observability):
        return None
    # No block at all still means traced — with whatever backends the environment names.
    if observability is None:
        from ..spec import ObservabilitySpec  # imported here: spec imports this module

        observability = ObservabilitySpec(exporters=default_exporters())
    factory = OBSERVERS.get(observability.provider)
    if factory is None:
        raise CapabilityError(
            f"observability provider {observability.provider!r} is not installed — "
            f"available: {OBSERVERS.names()} "
            "(install the adapter, e.g. `pip install agentship-sdk[observability]`)"
        )
    return factory(observability)


__all__ = [
    "ALLOW_SAAS_ENV",
    "EXPORTERS_ENV",
    "OBSERVERS",
    "ObserverFactory",
    "default_exporters",
    "saas_egress_allowed",
    "resolve_observer",
    "tracing_is_on",
]
