"""The :class:`Observer` port every tracing backend implements, plus its no-op default.

An :class:`Observer` is the one seam the runtime and engines call to emit a trace: it hands out
:class:`Span` context managers that nest into the canonical tree (§4.1) and takes a :class:`Usage`
roll-up per model call. The port is deliberately tiny and OTel-free so it can live in the kernel and
be swapped for a real OTel implementation (``agentship-observability``) or the :class:`NoOpObserver`
below (tracing disabled) without any caller change. Fail-open is a contract, not a courtesy — an
observer must never let a tracing fault break an agent run (§4.7).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any, Protocol, runtime_checkable

from .types import SpanKind, Usage


@runtime_checkable
class Span(Protocol):
    """A live span handed to the caller inside an :meth:`Observer.span` block.

    The caller sets attributes as work proceeds and, on failure, records the exception. Marking the
    error status is handled by :meth:`Observer.span` itself when the ``with`` block raises, so a
    caller rarely calls :meth:`set_error` directly — it exists for the branch that wants an
    error-status span without raising out of the block.
    """

    def set_attribute(self, key: str, value: Any) -> None:
        """Set one attribute on the span."""
        ...

    def set_attributes(self, attrs: Mapping[str, Any]) -> None:
        """Set several attributes at once."""
        ...

    def record_exception(self, exc: BaseException) -> None:
        """Attach an exception to the span (does not itself set error status)."""
        ...

    def set_error(self, exc: BaseException | None = None) -> None:
        """Mark the span's status as error, optionally recording ``exc``."""
        ...


class Observer(ABC):
    """The tracing port: a span factory plus a per-model-call usage sink.

    Implementations own the span tree's shape and backend wiring; callers only name a span, give its
    :class:`SpanKind` and opening attributes, and do work inside the ``with`` block. When the block
    raises, the implementation records the exception, sets error status, and re-raises unchanged so
    the run's error flow is untouched (§4.7). Everything is fail-open: an exporter fault is logged
    and swallowed, never propagated.
    """

    @abstractmethod
    def span(
        self, name: str, kind: SpanKind, attrs: Mapping[str, Any] | None = None
    ) -> Any:  # returns a context manager yielding Span
        """Open a child span ``name`` of ``kind`` and yield a :class:`Span` for its lifetime.

        Used as ``with observer.span("model", SpanKind.LLM, {...}) as span:``. The span nests under
        whatever span is active on the current task, so the tree shape (§4.1) is owned here, not by
        the engine. On a raise inside the block the implementation sets error status and re-raises.
        """

    @abstractmethod
    def on_model(self, usage: Usage) -> None:
        """Stamp GenAI cost/token/latency attributes from ``usage`` onto the current ``model`` span.

        Called by the LiteLLM callback (C3) once per model call. A no-op with a warning if the
        active span is not a model span, so a stray call never corrupts an unrelated span (§4.3).
        """

    @abstractmethod
    def current_trace_id(self) -> str | None:
        """Return the active trace id (hex) so the runtime can stamp ``RunContext.trace_id``.

        ``None`` when no span is active. The service echoes this as an ``X-Trace-Id`` response
        header so a client can correlate a request with its trace.
        """


class _NoOpSpan:
    """A :class:`Span` that drops every call — the span handed out when tracing is off."""

    def set_attribute(self, key: str, value: Any) -> None:
        """Ignore the attribute."""

    def set_attributes(self, attrs: Mapping[str, Any]) -> None:
        """Ignore the attributes."""

    def record_exception(self, exc: BaseException) -> None:
        """Ignore the exception."""

    def set_error(self, exc: BaseException | None = None) -> None:
        """Ignore the error status."""


class NoOpObserver(Observer):
    """The default observer: every method is a no-op, so a run works with tracing disabled.

    Selected when ``observability.provider`` is ``none`` (or nothing wired it). It still honours the
    ``span`` context-manager contract — it yields a :class:`_NoOpSpan` and re-raises any exception
    from the block unchanged — so callers need no ``if observer:`` guards anywhere.
    """

    @contextmanager
    def span(
        self, name: str, kind: SpanKind, attrs: Mapping[str, Any] | None = None
    ) -> Iterator[Span]:
        """Yield a throwaway span; still propagate any exception from the block."""
        yield _NoOpSpan()

    def on_model(self, usage: Usage) -> None:
        """Drop the usage — nothing is recorded when tracing is off."""

    def current_trace_id(self) -> str | None:
        """No trace is active, so there is no id to stamp."""
        return None
