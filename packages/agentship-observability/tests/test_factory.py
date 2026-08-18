"""The observer factory: provider memoization, the none/disabled path, and the SaaS gate (P07 · C5).

``build_observer`` is the seam the runtime uses. These tests pin its dispatch (none/disabled →
no-op, otel → ``OTelObserver``), prove the ``TracerProvider`` is built once per config (§DoD "one
process-global"), and prove the SaaS exporter is blocked unless opted in (§4.6).
"""

from __future__ import annotations

import pytest
from agentship.errors import CapabilityError
from agentship.observability import NoOpObserver
from agentship_observability import OTelObserver
from agentship_observability.config import ObservabilityConfig
from agentship_observability.factory import (
    _reset_providers_for_tests,
    build_observer,
    build_otel_observer,
    build_tracer_provider,
)


@pytest.fixture(autouse=True)
def _clean_providers():
    """Each test gets a fresh process-global provider cache."""
    _reset_providers_for_tests()
    yield
    _reset_providers_for_tests()


def test_build_observer_none_provider_is_noop() -> None:
    """``provider: none`` yields the no-op observer (untraced but unchanged run)."""
    obs = build_observer(ObservabilityConfig(provider="none"))
    assert isinstance(obs, NoOpObserver)


def test_build_observer_disabled_is_noop() -> None:
    """``enabled: false`` yields the no-op observer regardless of provider."""
    obs = build_observer(ObservabilityConfig(enabled=False))
    assert isinstance(obs, NoOpObserver)


def test_build_observer_otel_returns_otel_observer() -> None:
    """The default (otel) config builds a real ``OTelObserver``."""
    obs = build_observer(ObservabilityConfig())
    assert isinstance(obs, OTelObserver)


def test_tracer_provider_is_memoized_per_config() -> None:
    """Two builds with the same config share one provider; a different config gets its own."""
    a = build_tracer_provider(ObservabilityConfig())
    b = build_tracer_provider(ObservabilityConfig())
    assert a is b
    c = build_tracer_provider(ObservabilityConfig(exporters=["phoenix"]))
    assert c is not a


def test_saas_exporter_blocked_unless_opted_in() -> None:
    """LangSmith is refused by default and allowed only with ``allow_saas_exporter``."""
    with pytest.raises(CapabilityError):
        build_tracer_provider(ObservabilityConfig(exporters=["langsmith"]))
    provider = build_tracer_provider(
        ObservabilityConfig(exporters=["langsmith"], allow_saas_exporter=True)
    )
    assert provider is not None


def test_build_otel_observer_carries_capture_flag() -> None:
    """The observer inherits the config's PHI content-capture flag."""
    assert build_otel_observer(ObservabilityConfig()).capture_content is False
    assert build_otel_observer(ObservabilityConfig(capture_content=True)).capture_content is True
