"""Build an :class:`Observer` from an :class:`ObservabilityConfig` (§C5).

The public seam the rest of AgentShip calls. :func:`build_observer` maps a config to a concrete
observer: ``provider: none`` (or ``enabled: false``) yields the no-op observer so a run is untraced
but unchanged; ``provider: otel`` builds an :class:`OTelObserver` over a **process-global**
``TracerProvider``.

The provider is memoized (§DoD "one process-global"): every agent in a process shares one provider,
so exporters and their batching threads are created once, not per agent. The memo key is the config
signature that affects the pipeline (service name, exporters, sampling), so two agents with the same
observability block reuse the same provider and two with different blocks each get their own.
"""

from __future__ import annotations

import os

from agentship.errors import CapabilityError
from agentship.observability import NoOpObserver, Observer
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

from .config import ObservabilityConfig
from .exporters import build_processor
from .otel import OTelObserver

#: The OTel ``service.name`` for every AgentShip span, overridable via env for multi-service setups.
DEFAULT_SERVICE_NAME = "agentship"

#: Process-global cache of built providers, keyed by :func:`_provider_key`.
_PROVIDERS: dict[tuple, TracerProvider] = {}


def _service_name() -> str:
    """Resolve the ``service.name`` resource attribute (env override, then default)."""
    return os.getenv("AGENTSHIP_SERVICE_NAME", DEFAULT_SERVICE_NAME)


def _resolved_exporters(config: ObservabilityConfig) -> list[str]:
    """Return the exporter names to wire, enforcing the SaaS gate (§4.6).

    A SaaS exporter (LangSmith) ships spans off-box, so it is only allowed when the config opts in
    with ``allow_saas_exporter``. Otherwise it is a fail-fast :class:`CapabilityError` rather than a
    silent data-egress the operator did not ask for.
    """
    if config.uses_saas_exporter and not config.allow_saas_exporter:
        raise CapabilityError(
            "exporter 'langsmith' ships spans to a SaaS backend; set allow_saas_exporter: true "
            "to enable it"
        )
    return list(config.exporters)


def _provider_key(config: ObservabilityConfig) -> tuple:
    """Build the memo key: the config fields that change the provider's pipeline."""
    return (_service_name(), tuple(config.exporters), config.sample_ratio)


def build_tracer_provider(config: ObservabilityConfig) -> TracerProvider:
    """Return the process-global ``TracerProvider`` for ``config``, building it once.

    The provider carries the ``service.name`` resource and a ``ParentBased(TraceIdRatioBased)``
    sampler (so a child inherits its root's keep/drop decision) and registers one span processor per
    configured exporter.
    """
    key = _provider_key(config)
    provider = _PROVIDERS.get(key)
    if provider is not None:
        return provider

    resource = Resource.create({SERVICE_NAME: _service_name()})
    sampler = ParentBased(TraceIdRatioBased(config.sample_ratio))
    provider = TracerProvider(resource=resource, sampler=sampler)
    for name in _resolved_exporters(config):
        provider.add_span_processor(build_processor(name, config))

    _PROVIDERS[key] = provider
    return provider


def build_otel_observer(config: ObservabilityConfig | None = None) -> OTelObserver:
    """Build an :class:`OTelObserver` over the process-global provider for ``config``.

    Called by the ``agentship.observers`` entry point (``otel``). Defaults to a default config so a
    bare call still yields a working console-exporting observer.
    """
    config = config or ObservabilityConfig()
    provider = build_tracer_provider(config)
    return OTelObserver(provider, capture_content=config.capture_content)


def build_observer(config: ObservabilityConfig | None = None) -> Observer:
    """Map a config to a concrete observer: no-op when disabled/``none``, else the OTel observer."""
    config = config or ObservabilityConfig()
    if not config.enabled or config.provider == "none":
        return NoOpObserver()
    return build_otel_observer(config)


def _reset_providers_for_tests() -> None:
    """Clear the process-global provider cache. For tests that assert build-once behaviour."""
    _PROVIDERS.clear()
