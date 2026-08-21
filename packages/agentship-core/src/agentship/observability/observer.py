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

    def end(self) -> None:
        """Close the span, fixing its end time.

        Only spans opened with :meth:`Observer.start_span` need an explicit ``end`` — a ``with
        observer.span(...)`` block closes its span automatically on exit. The manual pair exists for
        callback-driven tracing (the LangChain handler), where a span is opened on a *start* event
        and closed on the matching *end* event, so the two never share one ``with`` block.
        """
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
    def start_span(
        self,
        name: str,
        kind: SpanKind,
        attrs: Mapping[str, Any] | None = None,
        *,
        parent: Span | None = None,
    ) -> Span:
        """Open a span and return it *without* a ``with`` block; the caller must :meth:`Span.end`.

        This is the manual counterpart to :meth:`span`, for callback-driven tracing: a span is
        opened on one event and closed on a later one (the LangChain handler opens a ``model`` span
        on ``on_chat_model_start`` and ends it on ``on_llm_end``). ``parent`` sets the span's parent
        explicitly — passing a :class:`Span` returned by an earlier ``start_span`` nests under it,
        wherever on the call stack the events fire; ``None`` nests under whatever span is active on
        the current task (so the first callback span lands under the runtime's root ``agent`` span).
        The span is not entered into the ambient context, so its error status must be set via
        :meth:`Span.set_error` rather than by a raising ``with`` block.
        """

    @abstractmethod
    def on_model(self, usage: Usage) -> None:
        """Stamp GenAI cost/token/latency attributes from ``usage`` onto the current ``model`` span.

        Called by the LiteLLM callback (C3) once per model call. A no-op with a warning if the
        active span is not a model span, so a stray call never corrupts an unrelated span (§4.3).
        """

    def annotate_model(self, attrs: Mapping[str, Any]) -> None:  # noqa: B027 - opt-in default no-op
        """Stamp extra attributes onto the active ``model`` span; no-op off one (fail-open).

        Used by the record/replay capture hook (§4.10) to write the request hash and the gated
        request/response payload onto the finished model span, next to the usage roll-up. Defaults
        to a no-op so a tracing-off or annotation-unaware observer simply ignores it; observers that
        track a current span override this to write the attributes.
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

    def end(self) -> None:
        """Ignore — a no-op span has nothing to close."""


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

    def start_span(
        self,
        name: str,
        kind: SpanKind,
        attrs: Mapping[str, Any] | None = None,
        *,
        parent: Span | None = None,
    ) -> Span:
        """Return a throwaway span — tracing is off, so nothing is recorded."""
        return _NoOpSpan()

    def on_model(self, usage: Usage) -> None:
        """Drop the usage — nothing is recorded when tracing is off."""

    def current_trace_id(self) -> str | None:
        """No trace is active, so there is no id to stamp."""
        return None
