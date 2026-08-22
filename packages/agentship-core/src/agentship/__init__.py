"""AgentShip — an open, recipe-first harness for agentic systems.

Author an agent (or a team) in YAML or Python and get the plumbing wired behind
small, stable seams. This module re-exports the kernel's public surface: the spec,
the identity backbone, the runtime, and the error taxonomy.
"""

from __future__ import annotations

from .context import Caller, RunContext, RunMode, get_run_context
from .engines.base import Engine, EngineCapabilities, Event, Result, ResumeToken
from .errors import (
    AgentShipError,
    CapabilityError,
    EngineNotFoundError,
    SpecError,
)
from .middleware import Middleware
from .primitives.model_router import (
    DefaultModelRouter,
    ModelRouter,
    resolve_model_router,
)
from .runtime import RunnableAgent, build_agent
from .spec import AgentSpec, MemberSpec, ModelParams, ObservabilitySpec, load_spec, resolve_code

__all__ = [
    "AgentSpec",
    "MemberSpec",
    "ModelParams",
    "ObservabilitySpec",
    "load_spec",
    "resolve_code",
    "Caller",
    "RunContext",
    "RunMode",
    "get_run_context",
    "RunnableAgent",
    "build_agent",
    "Middleware",
    "ModelRouter",
    "DefaultModelRouter",
    "resolve_model_router",
    "Engine",
    "EngineCapabilities",
    "Event",
    "ResumeToken",
    "Result",
    "AgentShipError",
    "SpecError",
    "CapabilityError",
    "EngineNotFoundError",
]
