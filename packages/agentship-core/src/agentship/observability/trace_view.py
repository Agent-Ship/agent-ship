"""The frozen :class:`TraceView` read-port over a finished span tree (Phase 07 DoD, §4.9).

This is the *one* artifact downstream phases read a run through. P12's ``Verifier.verify(scenario,
result, trace: TraceView, ctx)`` walks it (e.g. a tool-call assertion iterates ``tool_calls()``), so
its shape and method signatures are a frozen contract alongside SEMCONV. It is backend-agnostic —
a plain immutable tree with no OTel/Phoenix import — so verifiers run offline on a captured tree.

The tree is produced by an in-process capture (the ``RecordingObserver`` in core, or the OTel
capture processor in ``agentship-observability``); this module only defines how it is *read*.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from .semconv import SPAN_MODEL, TOOL_PREFIX
from .types import SpanKind


@dataclass(frozen=True)
class SpanNode:
    """One finished span: its frozen name/kind, attributes, status, and ordered children.

    ``name`` is a frozen §4.1 name (``"model"``, ``"tool.search"``, …); ``attrs`` are the frozen
    §4.2 ``gen_ai.*`` / ``agentship.*`` keys; ``status`` is ``"ok"`` or ``"error"``. Frozen and
    hashable so a captured tree is a safe, shareable value a verifier can hold across a run.
    """

    name: str
    kind: SpanKind
    attrs: Mapping[str, Any]
    status: str
    children: tuple[SpanNode, ...] = ()


class TraceView:
    """A read-only view of one run's finished span tree, walked by P12 verifiers.

    Consumers never mutate it — they call :meth:`spans` to find spans by name, :meth:`model_spans`
    to reach every LLM call (each already carrying tokens/cost/latency), and :meth:`tool_calls` to
    reach every tool invocation. The signatures here are the frozen contract P12 depends on.
    """

    def __init__(self, root: SpanNode) -> None:
        """Wrap the captured ``root`` :class:`SpanNode` of a single run."""
        self.root = root

    def _walk(self, node: SpanNode) -> Iterator[SpanNode]:
        """Yield ``node`` then each descendant in depth-first, parent-before-child order."""
        yield node
        for child in node.children:
            yield from self._walk(child)

    def spans(self, name: str | None = None) -> Iterable[SpanNode]:
        """Yield every span in the tree, or only those whose name equals ``name``.

        Depth-first from the root; with ``name`` omitted it yields the whole tree, so a caller can
        do its own filtering. This is the general accessor the two below specialise.
        """
        for node in self._walk(self.root):
            if name is None or node.name == name:
                yield node

    def model_spans(self) -> Iterable[SpanNode]:
        """Yield every ``model`` span — the LLM calls, each carrying ``gen_ai.usage.*`` + cost."""
        return self.spans(SPAN_MODEL)

    def tool_calls(self) -> Iterable[SpanNode]:
        """Yield every ``tool.<name>`` span — one per tool invocation, in execution order."""
        for node in self._walk(self.root):
            if node.name.startswith(TOOL_PREFIX):
                yield node
