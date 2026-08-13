"""``GraphConfig`` — the typed parse of a supervisor agent's ``graph:`` YAML block (Phase 02 · C1).

The ``graph`` template is entirely config-driven: how to classify the request, which specialists a
given intent routes to (and how they're dispatched), how to resolve their competing answers, the
retry cap, and which tools need human confirmation — all declared in YAML. This module validates
that block into a small tree of Pydantic models so a typo or a missing ``_default`` route fails at
load with a clear message rather than mid-run. It reuses the core ``ConflictPolicy`` and dispatch
``Strategy`` so each has a single definition.
"""

from __future__ import annotations

from agentship.primitives.conflict_resolver import ConflictPolicy
from agentship.primitives.dispatch import Strategy
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ClassifyConfig(BaseModel):
    """The single classify step: which model labels the request, and the allowed intents."""

    model_config = ConfigDict(extra="forbid")
    #: LiteLLM model id for the classifier (resolved via the ModelRouter at runtime).
    model: str
    #: The closed set of intents the classifier may return; anything else routes to ``_default``.
    intents: list[str]


class RouteEntry(BaseModel):
    """One row of the routing table: which specialists handle an intent, and how they're run."""

    model_config = ConfigDict(extra="forbid")
    specialists: list[str]
    strategy: Strategy = "single"


class RetryConfig(BaseModel):
    """The bounded-retry policy (C6): a hard attempt cap and which failure categories to retry."""

    model_config = ConfigDict(extra="forbid")
    max_attempts: int = 3
    on: list[str] = Field(default_factory=lambda: ["timeout", "specialist_error"])


class HitlConfig(BaseModel):
    """The human-in-the-loop policy (C5): whether writes need confirmation, and which tools do."""

    model_config = ConfigDict(extra="forbid")
    confirm_before_write: bool = False
    write_tools: list[str] = Field(default_factory=list)


class GraphConfig(BaseModel):
    """The whole ``graph:`` block: classify → routing → conflict resolution → retry → HITL.

    ``routing`` must contain a ``_default`` entry so every classified (or unclassifiable) intent has
    somewhere to go — enforced here so the graph never dead-ends at runtime.
    """

    model_config = ConfigDict(extra="forbid")
    classify: ClassifyConfig
    routing: dict[str, RouteEntry]
    conflict_resolver: ConflictPolicy
    retry: RetryConfig = Field(default_factory=RetryConfig)
    hitl: HitlConfig = Field(default_factory=HitlConfig)

    @model_validator(mode="after")
    def _require_default_route(self) -> GraphConfig:
        """Every routing table needs a ``_default`` fallback — reject one that lacks it."""
        if "_default" not in self.routing:
            raise ValueError(
                "routing must include a '_default' entry so an unknown intent has a fallback route"
            )
        return self
