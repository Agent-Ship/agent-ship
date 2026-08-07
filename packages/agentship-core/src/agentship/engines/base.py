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
from enum import StrEnum
from typing import TYPE_CHECKING, Any, ClassVar, Literal

from pydantic import BaseModel, Field

from ..errors import CapabilityError
from ..registry import Registry

if TYPE_CHECKING:  # avoid import cycles; these are only referenced in signatures
    from ..context import RunContext
    from ..spec import AgentSpec


class Modality(StrEnum):
    """An input modality an engine may accept (canonical §3.1).

    The kernel keeps ``multimodal_in`` as a ``set[Modality]`` so an engine declares
    exactly which non-text inputs it handles — a set, not a bool, because "accepts
    images" and "accepts audio" are independent claims that must not collapse into
    one on/off flag. ``TEXT`` is listed for completeness; every engine handles text.
    ``FILE`` covers a generic uploaded file that is not one of the typed media above.
    """

    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    PDF = "pdf"
    FILE = "file"


class EngineCapabilities(BaseModel):
    """An engine's honest declaration of what it supports (canonical, DESIGN §3.1).

    The kernel validates a spec against this before building. Anything not
    declared is treated as unsupported and requesting it fails fast (*declare,
    don't fake*). Every field defaults to off/none so declaring the class is
    non-breaking: an engine opts in only to what it truly supports.

    The field *shapes* are the canonical DESIGN §3.1 forms — ``providers`` is a
    ``set[str]``, ``hitl`` is a three-valued ``Literal``, ``multimodal_in`` is a
    ``set[Modality]``. These are widened up front on purpose: widening a field later
    (bool → set/Literal) would be a breaking change for anyone reading it, so the
    kernel commits to the final shape now even though later phases fill in the
    behaviour behind the richer values.
    """

    #: LiteLLM provider prefixes this engine can reach (e.g. ``{"openai", "anthropic"}``).
    #: A set: membership is order-free and each provider is an independent claim.
    #: Empty means *unconstrained* — the gate does not restrict the model's provider.
    providers: set[str] = Field(default_factory=set)
    #: Whether the engine can stream tokens/events for a turn.
    streaming: bool = False
    #: Whether the engine can call tools during a turn.
    tool_calling: bool = False
    #: Human-in-the-loop mode: ``"none"`` (no pauses), ``"interrupt"`` (pause the
    #: graph for input), or ``"deferred_tool"`` (a tool call awaits human approval).
    hitl: Literal["none", "interrupt", "deferred_tool"] = "none"
    #: Durable-execution mode: ``"none"`` (crash = fail), ``"checkpoint"`` (core
    #: drives ``resume``), or ``"workflow"`` (core re-attaches to an external runtime).
    durability: Literal["none", "checkpoint", "workflow"] = "none"
    #: Whether the engine's graph may contain cycles.
    cycles: bool = False
    #: Structured-output support: ``"none"``, ``"native"`` (engine validates), or
    #: ``"assisted"`` (core middleware validates + retries).
    structured_output: Literal["none", "native", "assisted"] = "none"
    #: The set of non-text input modalities the engine accepts (empty = text-only).
    #: A set, not a bool: "accepts images" and "accepts audio" are separate claims.
    multimodal_in: set[Modality] = Field(default_factory=set)
    #: Whether the engine supports live bidirectional (voice) streaming.
    live_bidi: bool = False
    #: Whether the engine can coordinate a multi-agent team (members).
    multi_agent: bool = False

    def assert_supports(self, cap: str, value: object = True) -> None:
        """Raise :class:`CapabilityError` unless this engine declares ``cap == value``.

        The canonical §13.5 low-level check: it takes only the capability name and the
        expected value (defaulting to ``True`` for the common boolean case), so there is
        a single canonical signature ``assert_supports(cap, value=True)``. It has no
        engine-name argument — engine-name context belongs to the spec-level
        :meth:`assert_supports_spec`, which knows the engine. Used by capability-gated
        methods (e.g. the default :meth:`Engine.stream`) to fail fast.
        """
        actual = getattr(self, cap, None)
        if actual != value:
            raise CapabilityError(
                f"capability {cap}={value!r} is not supported "
                f"(declares {cap}={actual!r}); use a different engine or remove "
                f"the requirement"
            )

    def assert_supports_spec(self, spec: AgentSpec) -> None:
        """Fail fast if these capabilities cannot satisfy what ``spec`` declares.

        The canonical §13.5 capability gate: every spec field that *implies* a
        capability is checked here, at build time, so a misconfiguration surfaces as
        an actionable :class:`~agentship.errors.CapabilityError` before the agent runs
        rather than as a confusing mid-run failure (*declare, don't fake*):

        - ``streaming`` → ``streaming``
        - ``output_schema`` (a declared schema) → ``structured_output != "none"``
        - ``members`` (a declared team) → ``multi_agent``
        - ``durability`` (other than ``"none"``) → ``durability != "none"``
        - ``model`` provider prefix → ``providers`` (skipped when ``providers`` empty)
        """
        if spec.streaming and not self.streaming:
            raise CapabilityError(
                f"engine {spec.engine!r} does not support streaming, but the spec sets "
                f"streaming: true — use a streaming engine or remove the field"
            )
        if spec.output_schema and self.structured_output == "none":
            raise CapabilityError(
                f"engine {spec.engine!r} does not support structured output, but the spec "
                f"declares output_schema: {spec.output_schema!r} — use a structured-output "
                f"engine or remove the field"
            )
        if spec.members and not self.multi_agent:
            raise CapabilityError(
                f"engine {spec.engine!r} does not support multi-agent coordination, but the "
                f"spec declares {len(spec.members)} member(s) — use a multi-agent engine or "
                f"remove the `members` field so they are not silently dropped"
            )
        if spec.durability != "none" and self.durability == "none":
            raise CapabilityError(
                f"engine {spec.engine!r} does not support durable execution, but the spec "
                f"requests durability: {spec.durability!r} — use a durable engine or remove "
                f"the field"
            )
        self._assert_provider_supported(spec)

    def _assert_provider_supported(self, spec: AgentSpec) -> None:
        """Gate the ``model:`` provider prefix against declared ``providers``.

        The provider is the segment before the first ``/`` in ``spec.model`` (e.g.
        ``"openai"`` in ``"openai/gpt-4o-mini"``), compared case-insensitively —
        LiteLLM (and the model seam's ``_provider_env_var``) lowercase the prefix, so
        the gate must too, or ``"OpenAI/…"`` would be wrongly rejected. If
        ``providers`` is empty the engine is *unconstrained* and any provider is
        allowed (this check is skipped). A model with no ``/`` prefix carries no
        provider to gate, so it is allowed too. Otherwise the provider must be in
        ``providers`` or the build fails fast — never a silent call to an
        unreachable provider.
        """
        if not self.providers or not spec.model or "/" not in spec.model:
            return
        provider = spec.model.split("/", 1)[0].lower()
        if provider not in self.providers:
            raise CapabilityError(
                f"engine {spec.engine!r} cannot reach provider {provider!r} (from "
                f"model {spec.model!r}); it declares providers {sorted(self.providers)} "
                f"— use a model on one of those providers or a different engine"
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

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Reject, at import, a *concrete* engine that forgets to declare ``capabilities``.

        The whole gate depends on every engine declaring what it supports, so a
        subclass that ships without a ``capabilities`` ClassVar is a latent bug —
        the gate would ``AttributeError`` at build time instead of failing honestly.
        Catching it here turns "engine forgot to declare capabilities" into a clear
        error the moment the module is imported. Abstract subclasses (those still
        carrying unimplemented ``@abstractmethod`` methods) are skipped, since they
        are seams, not shippable engines.
        """
        super().__init_subclass__(**kwargs)
        if getattr(cls, "__abstractmethods__", None):
            return  # still abstract — a seam, not a concrete engine
        if "capabilities" not in cls.__dict__ and not any(
            "capabilities" in base.__dict__ for base in cls.__mro__[1:] if base is not Engine
        ):
            raise TypeError(
                f"engine {cls.__name__!r} must declare a `capabilities` ClassVar "
                f"(an EngineCapabilities) so the capability gate knows what it supports"
            )

    @abstractmethod
    def build(self, spec: AgentSpec, authored: Any = None) -> Any:
        """Compile an :class:`AgentSpec` into an engine-native runnable artifact.

        Returns an opaque "compiled agent" that only this engine's ``run``/
        ``stream`` understand.

        ``authored`` is the optional Python-authored agent object a ``code:``
        reference produced (an object carrying its own ``.spec``). It is
        engine-specific and vendor-typed, so the *core* only forwards it opaquely;
        an engine that supports custom authoring (e.g. LangGraph's ``build_graph``
        path) inspects it, while engines that do not simply ignore it and build
        from ``spec`` alone. ``None`` means a declarative (YAML/spec-only) build.
        """

    @abstractmethod
    async def run(self, compiled: Any, text: str, ctx: RunContext) -> Result:
        """Execute one turn and return a :class:`Result`."""

    async def stream(self, compiled: Any, text: str, ctx: RunContext) -> AsyncIterator[Event]:
        """Stream events for one turn. Capability-gated on ``streaming``.

        The default asserts the capability (raising :class:`CapabilityError` since
        it is not declared here) — streaming engines override this method.
        """
        self.capabilities.assert_supports("streaming", True)
        raise NotImplementedError  # pragma: no cover - overridden by streaming engines
        yield  # pragma: no cover - makes this an async generator for type-checkers


#: The single shared registry of engines, discovered via the entry-point group.
ENGINES: Registry[type[Engine]] = Registry("agentship.engines", label="engine")


def register_engine(cls: type[Engine]) -> type[Engine]:
    """Register an engine class under its :attr:`~Engine.name`. Usable as a decorator."""
    return ENGINES.register(cls.name, cls)


def assert_spec_supported(engine: Engine, spec: AgentSpec) -> None:
    """Thin wrapper around :meth:`EngineCapabilities.assert_supports_spec`.

    Kept as the free-function entry point some callers (the CLI) import directly;
    it simply delegates to the engine's capabilities so the gate logic lives in one
    place (:meth:`EngineCapabilities.assert_supports_spec`, canonical §13.5).
    """
    engine.capabilities.assert_supports_spec(spec)
