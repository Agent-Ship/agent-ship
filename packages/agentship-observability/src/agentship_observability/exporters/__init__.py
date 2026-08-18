"""Exporter builders — each turns a configured backend name into an OTel ``SpanProcessor``.

One module per backend (console, phoenix, langfuse, langsmith); :func:`build_processor` in
``factory`` dispatches a config's ``exporters`` list to them. Every builder returns a ready
``SpanProcessor`` the tracer provider can register, so adding a backend is a one-file change plus a
line in the dispatch table.
"""

from __future__ import annotations

from .factory import build_processor

__all__ = ["build_processor"]
