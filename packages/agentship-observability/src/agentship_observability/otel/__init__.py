"""The OpenTelemetry implementation of the ``Observer`` port: kinds mapping + ``OTelObserver``."""

from __future__ import annotations

from .kinds import to_otel_kind
from .observer import OTelObserver

__all__ = ["OTelObserver", "to_otel_kind"]
