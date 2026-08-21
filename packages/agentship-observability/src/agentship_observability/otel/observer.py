"""``OTelObserver`` — the concrete :class:`Observer` over an OpenTelemetry ``TracerProvider`` (C1).

This is the default observer. It owns the span-tree shape (§4.1): every ``span()`` opens an OTel
span set as the current one, so a span opened inside another nests automatically (OTel's own
contextvars), and the tree is identical whichever engine runs. It stamps GenAI attributes (§4.2)
and, via :meth:`on_model`, rolls up cost/tokens/latency onto the active ``model`` span.

Fail-open is a hard rule (§4.7): a tracing/SDK fault is logged and swallowed so it can never break
an agent run. Only the *caller's* exception propagates — and when it does, the span records it and
is marked errored before it re-raises, so the run's error flow is untouched.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from agentship.observability import Observer, Span, SpanKind, Usage, semconv, usage_attributes
from opentelemetry import trace
from opentelemetry.trace import Span as OTelSpanType
from opentelemetry.trace import SpanKind as OTelSpanKind
from opentelemetry.trace import Status, StatusCode

from .kinds import to_otel_kind

_log = logging.getLogger("agentship.observability")

#: OTel accepts only str/bool/int/float (and sequences of those) as attribute values.
_SCALAR = (str, bool, int, float)


def _clean_attrs(attrs: Mapping[str, Any] | None) -> dict[str, Any]:
    """Coerce an attribute mapping into OTel-safe values, dropping ``None``.

    Anything not a scalar (or a list of scalars) is stringified rather than dropped, so a caller's
    attribute is never silently lost — it just arrives as text. ``None`` values are omitted because
    OTel rejects them.
    """
    cleaned: dict[str, Any] = {}
    for key, value in (attrs or {}).items():
        if value is None:
            continue
        if isinstance(value, _SCALAR):
            cleaned[key] = value
        elif isinstance(value, (list, tuple)) and all(isinstance(v, _SCALAR) for v in value):
            cleaned[key] = list(value)
        else:
            cleaned[key] = str(value)
    return cleaned


class _OTelSpan:
    """The :class:`Span` handed to callers — a thin writer over a live OTel span."""

    def __init__(self, span: OTelSpanType) -> None:
        """Bind the OTel span this handle writes through."""
        self._span = span

    def set_attribute(self, key: str, value: Any) -> None:
        """Set one attribute, coercing it to an OTel-safe value first."""
        cleaned = _clean_attrs({key: value})
        if cleaned:
            self._span.set_attribute(key, cleaned[key])

    def set_attributes(self, attrs: Mapping[str, Any]) -> None:
        """Set several attributes at once (each coerced, ``None`` dropped)."""
        self._span.set_attributes(_clean_attrs(attrs))

    def record_exception(self, exc: BaseException) -> None:
        """Attach the exception to the span (does not itself set error status)."""
        self._span.record_exception(exc)

    def set_error(self, exc: BaseException | None = None) -> None:
        """Mark the span's status ERROR, recording ``exc`` when given."""
        if exc is not None:
            self._span.record_exception(exc)
        self._span.set_status(Status(StatusCode.ERROR))

    def end(self) -> None:
        """End the underlying OTel span, fixing its end time (for :meth:`Observer.start_span`)."""
        self._span.end()


class _NoOpSpan:
    """Handed out only when opening a real span itself failed — keeps the caller's block running."""

    def set_attribute(self, key: str, value: Any) -> None:
        """Ignore."""

    def set_attributes(self, attrs: Mapping[str, Any]) -> None:
        """Ignore."""

    def record_exception(self, exc: BaseException) -> None:
        """Ignore."""

    def set_error(self, exc: BaseException | None = None) -> None:
        """Ignore."""

    def end(self) -> None:
        """Ignore — a no-op span has nothing to close."""


class OTelObserver(Observer):
    """The OpenTelemetry :class:`Observer`: a span factory + per-model-call usage sink.

    Construct with a configured ``TracerProvider`` (the factory builds one process-global provider).
    ``capture_content`` mirrors the PHI gate — when false, content-bearing attributes are never set,
    so prompt/response text cannot leave the process even if a caller passes it.
    """

    def __init__(
        self, tracer_provider: trace.TracerProvider, *, capture_content: bool = False
    ) -> None:
        """Bind a tracer from ``tracer_provider`` and record the PHI content-capture flag."""
        self._tracer = tracer_provider.get_tracer("agentship")
        self.capture_content = capture_content

    @contextmanager
    def span(
        self, name: str, kind: SpanKind, attrs: Mapping[str, Any] | None = None
    ) -> Iterator[Span]:
        """Open an OTel span ``name`` of ``kind`` (nested under the current one) and yield a handle.

        On a raise inside the block, OTel records the exception and sets ERROR status before it
        re-raises. If opening the span itself fails, a no-op handle is yielded so the caller's work
        still runs (fail-open, §4.7).
        """
        try:
            cm = self._tracer.start_as_current_span(
                name,
                kind=to_otel_kind(kind),
                attributes=_clean_attrs(attrs),
                record_exception=True,
                set_status_on_exception=True,
            )
        except Exception:  # noqa: BLE001 - tracing must never break the run
            _log.warning("observability.span.failed name=%s (tracing skipped)", name, exc_info=True)
            yield _NoOpSpan()
            return
        with cm as otel_span:
            yield _OTelSpan(otel_span)

    def start_span(
        self,
        name: str,
        kind: SpanKind,
        attrs: Mapping[str, Any] | None = None,
        *,
        parent: Span | None = None,
    ) -> Span:
        """Open an OTel span without entering it as current; the caller must :meth:`Span.end` it.

        The manual counterpart to :meth:`span`, for the callback-driven tracer. ``parent`` nests the
        span explicitly: a :class:`_OTelSpan` from an earlier ``start_span`` becomes the parent via
        its OTel context, so the model→tool tree is rebuilt from ``run_id``/``parent_run_id`` even
        though the events fire as separate calls. With ``parent=None`` the ambient context is used,
        so the first callback span lands under the open root ``agent`` span. Fail-open: if opening
        fails, a no-op handle is returned so the run continues untraced (§4.7).
        """
        try:
            context = None
            if isinstance(parent, _OTelSpan):
                context = trace.set_span_in_context(parent._span)
            otel_span = self._tracer.start_span(
                name,
                context=context,
                kind=to_otel_kind(kind),
                attributes=_clean_attrs(attrs),
            )
            return _OTelSpan(otel_span)
        except Exception:  # noqa: BLE001 - tracing must never break the run
            _log.warning("observability.start_span.failed name=%s (tracing skipped)", name,
                         exc_info=True)
            return _NoOpSpan()

    def _is_model_span(self, span: OTelSpanType) -> bool:
        """Return whether ``span`` is the ``model`` span usage should land on.

        Prefers the span name (``model``); falls back to the OTel kind (``CLIENT``, which only the
        model span uses) for spans whose name is not exposed.
        """
        if getattr(span, "name", None) == semconv.SPAN_MODEL:
            return True
        return getattr(span, "kind", None) == OTelSpanKind.CLIENT

    def on_model(self, usage: Usage) -> None:
        """Stamp GenAI + OpenInference cost/token/latency attributes onto the active model span.

        A no-op (with a warning) when the current span is not a model span, so a stray callback
        never corrupts an unrelated span (§4.3). Fail-open — any error here is swallowed.
        """
        try:
            span = trace.get_current_span()
            if not span.is_recording() or not self._is_model_span(span):
                _log.warning("on_model called off a model span; usage dropped")
                return
            span.set_attributes(_clean_attrs(usage_attributes(usage)))
        except Exception:  # noqa: BLE001 - tracing must never break the run
            _log.warning("observability.on_model.failed (usage dropped)", exc_info=True)

    def annotate_model(self, attrs: Mapping[str, Any]) -> None:
        """Stamp extra attributes (replay hash + gated payload) onto the active model span.

        A no-op (with a warning) off a model span, mirroring :meth:`on_model`. Fail-open — any
        error is swallowed so the capture hook can never break the model call (§4.7).
        """
        try:
            span = trace.get_current_span()
            if not span.is_recording() or not self._is_model_span(span):
                _log.warning("annotate_model called off a model span; attributes dropped")
                return
            span.set_attributes(_clean_attrs(attrs))
        except Exception:  # noqa: BLE001 - tracing must never break the run
            _log.warning("observability.annotate_model.failed (attributes dropped)", exc_info=True)

    def current_trace_id(self) -> str | None:
        """Return the active trace id as 32-hex, or ``None`` when no valid span is active."""
        ctx = trace.get_current_span().get_span_context()
        if ctx is None or not ctx.is_valid:
            return None
        return format(ctx.trace_id, "032x")
