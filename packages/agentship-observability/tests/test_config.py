"""``ObservabilityConfig`` defaults and validation (P07 · C5).

The defaults ARE the safe profile — tracing on, console exporter, PHI content capture off — so these
tests pin them so a future edit can't quietly loosen the privacy posture. Validation covers the
sampling-ratio bound and the ``extra="forbid"`` typo guard.
"""

from __future__ import annotations

import pytest
from agentship_observability.config import ObservabilityConfig
from pydantic import ValidationError


def test_defaults_are_the_safe_profile() -> None:
    """A bare config traces via OTel to console, with content capture off and ids hashed."""
    config = ObservabilityConfig()
    assert config.enabled is True
    assert config.provider == "otel"
    assert config.exporters == ["console"]
    assert config.capture_content is False
    assert config.hash_user_id is True
    assert config.sample_ratio == 1.0
    assert config.allow_saas_exporter is False


def test_sample_ratio_out_of_range_is_rejected() -> None:
    """A ratio outside 0.0–1.0 is a validation error, not a silently clamped value."""
    with pytest.raises(ValidationError):
        ObservabilityConfig(sample_ratio=1.5)
    with pytest.raises(ValidationError):
        ObservabilityConfig(sample_ratio=-0.1)


def test_unknown_field_is_rejected() -> None:
    """A typo'd key fails fast rather than being ignored (``extra='forbid'``)."""
    with pytest.raises(ValidationError):
        ObservabilityConfig(captur_content=True)


def test_unknown_exporter_name_is_rejected() -> None:
    """An exporter not in the known set is a validation error at parse time."""
    with pytest.raises(ValidationError):
        ObservabilityConfig(exporters=["nope"])


def test_uses_saas_exporter_reflects_langsmith() -> None:
    """``uses_saas_exporter`` is true exactly when LangSmith is among the exporters."""
    assert ObservabilityConfig(exporters=["console"]).uses_saas_exporter is False
    assert ObservabilityConfig(exporters=["langsmith"]).uses_saas_exporter is True
