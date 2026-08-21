"""``ObservabilityConfig`` — the ``observability:`` config surface (C5).

One small Pydantic model parses the ``observability:`` block from an agent's YAML (secrets come from
the environment, never the file). Defaults are the safe ones: tracing on, the OTel provider, the
``console`` exporter (which needs no running backend), content capture **off** (the PHI gate), user
ids hashed, full sampling, and SaaS exporters blocked. Phoenix is the recommended production OSS
exporter — a one-line ``exporters: [phoenix]`` swap — but is not the zero-config default because it
needs a collector to point at.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

#: The exporter names the factory knows how to build. Extend here + in ``exporters/factory.py``.
ExporterName = Literal["console", "phoenix", "langfuse", "langsmith", "opik"]


class ObservabilityConfig(BaseModel):
    """Declarative tracing config for one agent; the factory turns it into an ``Observer``.

    ``provider`` selects the implementation: ``otel`` (the default) builds an ``OTelObserver`` over
    a process-global ``TracerProvider``; ``none`` yields the no-op observer so a run is untraced but
    unchanged. ``exporters`` is a list so a span tree can fan out to several backends at once.
    """

    model_config = {"extra": "forbid"}

    #: Master switch. When false the factory yields the no-op observer regardless of ``provider``.
    enabled: bool = True
    #: The observer implementation: ``otel`` (default) or ``none`` (no-op).
    provider: Literal["otel", "none"] = "otel"
    #: Backends to export to; every span tree is sent to each. Default console (needs no backend).
    exporters: list[ExporterName] = Field(default_factory=lambda: ["console"])
    #: PHI gate — when false, prompt/response content is never put on a span (§4.6). Default off.
    capture_content: bool = False
    #: Hash the caller's user id on spans so a raw id never lands in a trace store. Default on.
    hash_user_id: bool = True
    #: Parent-based sampling ratio, 0.0–1.0. 1.0 keeps every trace; lower drops a fraction.
    sample_ratio: float = 1.0
    #: Allow a SaaS exporter (LangSmith) to receive content-bearing spans. Off in the PHI profile.
    allow_saas_exporter: bool = False

    @field_validator("sample_ratio")
    @classmethod
    def _ratio_in_range(cls, value: float) -> float:
        """Reject a sampling ratio outside 0.0–1.0 — a typo here silently loses or floods traces."""
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"sample_ratio must be between 0.0 and 1.0, got {value}")
        return value

    @property
    def uses_saas_exporter(self) -> bool:
        """Whether any configured exporter ships spans to a SaaS backend (currently LangSmith)."""
        return "langsmith" in self.exporters
