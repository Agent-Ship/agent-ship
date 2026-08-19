"""An in-process :class:`Observer` that records the span tree in memory — no OTel required.

:class:`RecordingObserver` is the backend-agnostic capture the design calls for (§4.9): it builds
the same span tree an OTel exporter would, but keeps it in memory and exposes it as a frozen
:class:`~agentship.observability.trace_view.TraceView`. That makes it the substrate for offline
verifiers (P12 runs against a captured tree with no collector) and the honest test double for the
runtime wiring — asserting on the tree an observer *should* have built, without standing up OTel.

Nesting is tracked per task with a :class:`~contextvars.ContextVar` stack, so concurrent turns and
concurrent nodes each parent their spans correctly (the same guarantee OTel's own context gives).

This is deliberately *not* OTel's ``InMemorySpanExporter``: core is vendor-free by contract (it
cannot import OpenTelemetry), the base install ships without OTel, and offline verifiers consume our
own ``SpanNode``/``TraceView`` tree, not OTel's flat ``ReadableSpan`` list. It is the in-memory
implementation of our own Observer port. To keep this double honest, a parity guard in
``agentship-observability`` (``tests/test_observer_parity.py``) runs the same interaction through
both this recorder and the real OTel observer and asserts the trees agree, so the vendor-free
capture cannot silently drift from the production pipeline.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from .observer import Observer, Span
from .semconv import (
    AS_COST_USD,
    AS_LATENCY_MS,
    GEN_AI_RESPONSE_FINISH_REASONS,
    GEN_AI_RESPONSE_MODEL,
    GEN_AI_SYSTEM,
    GEN_AI_USAGE_INPUT_TOKENS,
    GEN_AI_USAGE_OUTPUT_TOKENS,
    OI_TOKEN_COUNT_COMPLETION,
    OI_TOKEN_COUNT_PROMPT,
    OI_TOKEN_COUNT_TOTAL,
)
from .trace_view import SpanNode, TraceView
from .types import SpanKind, Usage

_log = logging.getLogger("agentship.observability")


@dataclass
class _Recording:
    """A mutable span being recorded; frozen into a :class:`SpanNode` once the trace is read."""

    name: str
    kind: SpanKind
    attrs: dict[str, Any]
    status: str = "ok"
    children: list[_Recording] = field(default_factory=list)

    def freeze(self) -> SpanNode:
        """Convert this mutable record (and its subtree) into an immutable :class:`SpanNode`."""
        return SpanNode(
            name=self.name,
            kind=self.kind,
            attrs=dict(self.attrs),
            status=self.status,
            children=tuple(child.freeze() for child in self.children),
        )


class _RecordingSpan:
    """The :class:`Span` handed to callers — writes straight onto its backing ``_Recording``."""

    def __init__(self, record: _Recording) -> None:
        """Bind the mutable record this span writes through."""
        self._record = record

    def set_attribute(self, key: str, value: Any) -> None:
        """Record one attribute."""
        self._record.attrs[key] = value

    def set_attributes(self, attrs: Mapping[str, Any]) -> None:
        """Record several attributes."""
        self._record.attrs.update(attrs)

    def record_exception(self, exc: BaseException) -> None:
        """Note the exception type/message on the span (no status change here)."""
        self._record.attrs["exception.type"] = type(exc).__name__
        self._record.attrs["exception.message"] = str(exc)

    def set_error(self, exc: BaseException | None = None) -> None:
        """Mark the span errored, recording ``exc`` when given."""
        self._record.status = "error"
        if exc is not None:
            self.record_exception(exc)


class RecordingObserver(Observer):
    """Record spans into an in-memory tree and expose it as a :class:`TraceView`.

    One instance captures one trace: the first root span opened mints the trace id. Suitable for
    tests and offline verifiers; not an exporter (nothing leaves the process). Concurrency-safe via
    a per-task current-span stack.
    """

    def __init__(self) -> None:
        """Start an empty recording with no active span and no trace id yet."""
        self._roots: list[_Recording] = []
        self._current: ContextVar[_Recording | None] = ContextVar("recording_current", default=None)
        self._trace_id: str | None = None

    @contextmanager
    def span(
        self, name: str, kind: SpanKind, attrs: Mapping[str, Any] | None = None
    ) -> Iterator[Span]:
        """Open a child span under the current one, recording error status if the block raises."""
        record = _Recording(name=name, kind=kind, attrs=dict(attrs or {}))
        parent = self._current.get()
        if parent is None:
            self._roots.append(record)
            if self._trace_id is None:
                self._trace_id = uuid.uuid4().hex
        else:
            parent.children.append(record)
        token = self._current.set(record)
        try:
            yield _RecordingSpan(record)
        except GeneratorExit:
            # A consumer abandoning a stream is cleanup, not a failure — leave the span "ok".
            raise
        except BaseException as exc:
            record.status = "error"
            _RecordingSpan(record).record_exception(exc)
            raise
        finally:
            self._current.reset(token)

    def on_model(self, usage: Usage) -> None:
        """Stamp usage onto the current span if it is a model span; warn and no-op otherwise."""
        current = self._current.get()
        if current is None or current.kind is not SpanKind.LLM:
            _log.warning("on_model called with no active model span; usage dropped")
            return
        current.attrs.update(
            {
                GEN_AI_SYSTEM: usage.provider,
                GEN_AI_USAGE_INPUT_TOKENS: usage.input_tokens,
                GEN_AI_USAGE_OUTPUT_TOKENS: usage.output_tokens,
                GEN_AI_RESPONSE_FINISH_REASONS: list(usage.finish_reasons),
                OI_TOKEN_COUNT_PROMPT: usage.input_tokens,
                OI_TOKEN_COUNT_COMPLETION: usage.output_tokens,
                OI_TOKEN_COUNT_TOTAL: usage.input_tokens + usage.output_tokens,
                AS_LATENCY_MS: usage.latency_ms,
            }
        )
        if usage.cost_usd is not None:
            current.attrs[AS_COST_USD] = usage.cost_usd
        if usage.response_model is not None:
            current.attrs[GEN_AI_RESPONSE_MODEL] = usage.response_model

    def annotate_model(self, attrs: Mapping[str, Any]) -> None:
        """Stamp extra attributes onto the current model span; warn and no-op otherwise."""
        current = self._current.get()
        if current is None or current.kind is not SpanKind.LLM:
            _log.warning("annotate_model called with no active model span; attributes dropped")
            return
        current.attrs.update(attrs)

    def current_trace_id(self) -> str | None:
        """Return the trace id minted when the first root span opened (``None`` before that)."""
        return self._trace_id

    def trace_view(self, root_index: int = 0) -> TraceView:
        """Return a :class:`TraceView` over the captured root span (the ``root_index``-th one).

        A single interaction opens exactly one root ``agent`` span, so the default index fits the
        common case; the argument exists only for the rare test that opens several roots.
        """
        return TraceView(self._roots[root_index].freeze())

    @property
    def roots(self) -> tuple[SpanNode, ...]:
        """Every captured root span, frozen — for tests that assert on more than one trace."""
        return tuple(record.freeze() for record in self._roots)
