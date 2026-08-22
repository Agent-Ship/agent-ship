"""Observability contract (Phase 07) — the vendor-free tracing port and its frozen data shapes.

This package is the kernel half of tracing: the :class:`~agentship.observability.observer.Observer`
port, the :class:`~agentship.observability.types.SpanKind` / ``Usage`` value types, the frozen
SEMCONV span-name/attribute-key constants, and the
:class:`~agentship.observability.trace_view.TraceView` read-port that P12 consumes. It carries **no
OpenTelemetry dependency** — the OTel implementation, exporters, and LiteLLM callback live in the
separate ``agentship-observability`` package (design §4.6), so an engine or eval hook can read the
tracing contract with nothing but the kernel installed.

:class:`~agentship.observability.recorder.RecordingObserver` is the in-process, backend-agnostic
capture used by offline verifiers and by the runtime tests.
"""

from __future__ import annotations

from .attributes import usage_attributes
from .capture import replay_attributes, request_hash
from .observer import NoOpObserver, Observer, Span, current_observer, get_observer
from .phi import hashed_user_id
from .recorder import RecordingObserver
from .redaction import redact_pii
from .registry import OBSERVERS, ObserverFactory, resolve_observer
from .trace_view import SpanNode, TraceView
from .types import SpanKind, Usage

__all__ = [
    "NoOpObserver",
    "OBSERVERS",
    "Observer",
    "ObserverFactory",
    "RecordingObserver",
    "resolve_observer",
    "Span",
    "SpanKind",
    "SpanNode",
    "TraceView",
    "Usage",
    "current_observer",
    "get_observer",
    "hashed_user_id",
    "redact_pii",
    "replay_attributes",
    "usage_attributes",
    "request_hash",
]
