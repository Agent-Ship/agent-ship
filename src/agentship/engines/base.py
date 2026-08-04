"""The engine seam: :class:`Engine`, :class:`EngineCapabilities`, and the gate.

An :class:`Engine` translates an :class:`~agentship.spec.AgentSpec` into something
the harness can ``build`` then ``run``/``stream``. Each engine declares, honestly,
what it supports via :class:`EngineCapabilities`. The kernel's rule (architecture
§4) is *declare, don't fake*: if a spec asks for something the engine has not
declared, :func:`assert_spec_supported` raises :class:`~agentship.errors.CapabilityError`
at build time — never a silent degrade, never a confusing mid-run failure.

Only the seam lives here; concrete engines live alongside (``echo.py`` today,
vendor engines in later phases). The single shared :data:`ENGINES` registry holds
them, populated in-code and by the ``agentship.engines`` entry-point group.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any, ClassVar

from pydantic import BaseModel

from ..errors import CapabilityError
from ..registry import Registry

if TYPE_CHECKING:  # avoid import cycles; these are only referenced in signatures
    from ..context import RunContext
    from ..spec import AgentSpec


class EngineCapabilities(BaseModel):
    """An engine's honest declaration of what it supports.

    The kernel validates a spec against this before building. Anything not
    declared is treated as unsupported and requesting it fails fast.
    """

    streaming: bool = False
    tool_calling: bool = False
    structured_output: bool = False
    multi_agent: bool = False
    resume: bool = False

    def assert_supports(self, engine_name: str, cap: str, value: object = True) -> None:
        """Raise :class:`CapabilityError` unless this engine declares ``cap == value``.

        Low-level check used by capability-gated methods (e.g. the default
        :meth:`Engine.stream`) to fail fast with an actionable message.
        """
        actual = getattr(self, cap, None)
        if actual != value:
            raise CapabilityError(
                f"engine {engine_name!r} does not support {cap}={value!r} "
                f"(declares {cap}={actual!r}); use a different engine or remove "
                f"the requirement"
            )


class Result(BaseModel):
    """The outcome of a run: the agent's output."""

    model_config = {"arbitrary_types_allowed": True}

    output: Any = None


class Event(BaseModel):
    """One streamed event. ``type`` follows the SSE-style contract (content, done, …)."""

    model_config = {"arbitrary_types_allowed": True}

    type: str
    data: Any = None


class Engine(ABC):
    """The runtime seam every engine implements.

    A concrete engine sets :attr:`name` and :attr:`capabilities`, then implements
    :meth:`build` and :meth:`run`. :meth:`stream` is capability-gated: the default
    honestly reports "not supported" via :class:`~agentship.errors.CapabilityError`,
    so an engine that does not override it cannot silently pretend to stream.
    """

    #: Stable engine name used in ``AgentSpec.engine`` and the registry.
    name: ClassVar[str]
    #: What this engine supports; the kernel validates specs against it.
    capabilities: ClassVar[EngineCapabilities]

    @abstractmethod
    def build(self, spec: AgentSpec) -> Any:
        """Compile an :class:`AgentSpec` into an engine-native runnable artifact.

        Returns an opaque "compiled agent" that only this engine's ``run``/
        ``stream`` understand.
        """

    @abstractmethod
    async def run(self, compiled: Any, text: str, ctx: RunContext) -> Result:
        """Execute one turn and return a :class:`Result`."""

    async def stream(self, compiled: Any, text: str, ctx: RunContext) -> AsyncIterator[Event]:
        """Stream events for one turn. Capability-gated on ``streaming``.

        The default asserts the capability (raising :class:`CapabilityError` since
        it is not declared here) — streaming engines override this method.
        """
        self.capabilities.assert_supports(self.name, "streaming", True)
        raise NotImplementedError  # pragma: no cover - overridden by streaming engines
        yield  # pragma: no cover - makes this an async generator for type-checkers


#: The single shared registry of engines, discovered via the entry-point group.
ENGINES: Registry[type[Engine]] = Registry("agentship.engines", label="engine")


def register_engine(cls: type[Engine]) -> type[Engine]:
    """Register an engine class under its :attr:`~Engine.name`. Usable as a decorator."""
    return ENGINES.register(cls.name, cls)


def assert_spec_supported(engine: Engine, spec: AgentSpec) -> None:
    """Fail fast if ``engine`` cannot satisfy what ``spec`` declares.

    This is the capability gate. Every spec field that *implies* an engine
    capability is checked here, at build time, so a misconfiguration surfaces as
    an actionable :class:`~agentship.errors.CapabilityError` before the agent runs:

    - ``streaming`` → ``capabilities.streaming``
    - ``output`` (a declared schema) → ``capabilities.structured_output``
    - ``members`` (a declared team) → ``capabilities.multi_agent``
    """
    caps = engine.capabilities
    if spec.streaming and not caps.streaming:
        raise CapabilityError(
            f"engine {spec.engine!r} does not support streaming, but the spec sets "
            f"streaming: true — use a streaming engine or remove the field"
        )
    if spec.output and not caps.structured_output:
        raise CapabilityError(
            f"engine {spec.engine!r} does not support structured output, but the spec "
            f"declares output: {spec.output!r} — use a structured-output engine or "
            f"remove the field"
        )
    if spec.members and not caps.multi_agent:
        raise CapabilityError(
            f"engine {spec.engine!r} does not support multi-agent coordination, but the "
            f"spec declares {len(spec.members)} member(s) — use a multi-agent engine or "
            f"remove the `members` field so they are not silently dropped"
        )
