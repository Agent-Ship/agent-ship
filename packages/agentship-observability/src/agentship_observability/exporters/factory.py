"""Dispatch a config's ``exporters`` list to per-backend processor builders (§C4).

One tiny lookup table maps each exporter name to its module's ``build(config)``. Adding a backend is
a one-line change here plus its module. An unknown name is a fail-fast :class:`CapabilityError` with
the known names listed, so a typo in YAML surfaces at build time, not as silent missing traces.
"""

from __future__ import annotations

from collections.abc import Callable

from agentship.errors import CapabilityError
from opentelemetry.sdk.trace.export import SpanProcessor

from ..config import ObservabilityConfig
from . import console, langfuse, langsmith, phoenix

#: Exporter name → builder. Keys must match ``config.ExporterName``.
_BUILDERS: dict[str, Callable[[ObservabilityConfig], SpanProcessor]] = {
    "console": console.build,
    "phoenix": phoenix.build,
    "langfuse": langfuse.build,
    "langsmith": langsmith.build,
}


def build_processor(name: str, config: ObservabilityConfig) -> SpanProcessor:
    """Build the ``SpanProcessor`` for one exporter ``name``.

    Raises :class:`CapabilityError` (listing the known names) when ``name`` is not a registered
    exporter, so a misconfigured ``exporters:`` entry fails loudly at build time.
    """
    builder = _BUILDERS.get(name)
    if builder is None:
        known = ", ".join(sorted(_BUILDERS))
        raise CapabilityError(f"unknown exporter {name!r}; known exporters: {known}")
    return builder(config)
