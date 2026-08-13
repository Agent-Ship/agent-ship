"""The base class every engine implements, and the check that guards it.

An :class:`Engine` turns an :class:`~agentship.spec.AgentSpec` into something we can
``build`` then ``run``/``stream``. Each engine states what it supports via
:class:`EngineCapabilities`; if a spec asks for more, :func:`assert_spec_supported`
raises at build time instead of failing halfway through a run. Engines are found by
name in the shared :data:`ENGINES` registry. See DESIGN §3.1/§4.
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
    """A kind of input an engine may accept (text, image, audio, video, pdf, file).

    Used as a set in ``multimodal_in`` so "accepts images" and "accepts audio" are
    independent claims, not one on/off flag. See DESIGN §3.1.
    """

    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    PDF = "pdf"
    FILE = "file"


class EngineCapabilities(BaseModel):
    """What an engine can do. The spec is checked against this before building.

    Anything not declared is treated as unsupported, so requesting it fails fast
    rather than mid-run. Every field defaults to off/none — an engine opts in only to
    what it truly supports. Field shapes are fixed now (set/Literal, not bool) so
    later phases can add behaviour without a breaking change. See DESIGN §3.1.
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

        The low-level check (default ``value=True`` for the common boolean case), used
        by methods like the default :meth:`Engine.stream` to fail fast.
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

        Each spec field that needs a capability is checked here, at build time, so a
        mismatch raises a clear :class:`~agentship.errors.CapabilityError` before the
        agent runs. The rules (see DESIGN §13.5):

        - ``streaming`` → ``streaming``
        - ``output_schema`` (a declared schema) → ``structured_output != "none"``
        - ``members`` (a declared team) → ``multi_agent``
        - ``durability`` (other than ``"none"``) → the engine's ``durability`` must **match**
          the requested mode (a ``checkpoint`` spec needs a ``checkpoint`` engine, a
          ``workflow`` spec a ``workflow`` engine — they select different resume methods)
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
        if spec.durability != "none" and spec.durability != self.durability:
            engine_mode = (
                "no durable execution"
                if self.durability == "none"
                else f"durability {self.durability!r}"
            )
            raise CapabilityError(
                f"engine {spec.engine!r} supports {engine_mode}, but the spec requests "
                f"durability: {spec.durability!r} — use an engine whose durability matches, or "
                f"remove the field"
            )
        self._assert_provider_supported(spec)

    def _assert_provider_supported(self, spec: AgentSpec) -> None:
        """Check the model's provider prefix is one this engine declares.

        The provider is the part before the first ``/`` in ``spec.model`` (e.g.
        ``"openai"`` in ``"openai/gpt-4o-mini"``), matched lower-case. Skipped when the
        engine lists no providers (unconstrained) or the model has no prefix; otherwise
        an unlisted provider fails the build rather than calling something unreachable.
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
    """The outcome of a run: the agent's output, plus a resume ticket for durable runs.

    ``resume_token`` is minted by a durable engine after a terminal or interrupted run so the
    caller (``/tasks`` in P09) can persist it and later drive :meth:`Engine.resume`. It is ``None``
    for non-durable runs — an engine that does not checkpoint never mints one. ``interrupt`` is set
    (and ``output`` left ``None``) when the run paused for human input: it carries the payload the
    node passed to ``interrupt(...)`` so the client can render a confirm dialog, and the run is
    continued by :meth:`Engine.resume` with the human's decision.
    """

    model_config = {"arbitrary_types_allowed": True}

    output: Any = None
    resume_token: ResumeToken | None = None
    interrupt: dict | None = None


class Event(BaseModel):
    """One streamed event. ``type`` follows the SSE-style contract (content, done, …)."""

    model_config = {"arbitrary_types_allowed": True}

    type: str
    data: Any = None


class ResumeToken(BaseModel):
    """An opaque ticket an engine mints so a crashed/paused run can resume.

    One shape for every engine: :attr:`engine` says who minted it and :attr:`blob`
    holds whatever that engine needs to continue. Core never reads ``blob`` — it
    stores it verbatim and hands it back to the same engine's :meth:`Engine.resume`.
    That is what lets storage round-trip a token without knowing engine internals.
    See DESIGN §13.1.
    """

    #: The name of the engine that minted this token — the only engine that may
    #: interpret its ``blob``. :meth:`Engine.resume` rejects a token whose ``engine``
    #: does not match, so a token can never be replayed on the wrong engine.
    engine: str
    #: Everything the minting engine needs to resume, opaque to core. Stored verbatim
    #: and never inspected outside the engine that produced it.
    blob: dict = Field(default_factory=dict)


# ``Result.resume_token`` forward-references ``ResumeToken`` (defined just above); with
# ``from __future__ import annotations`` the field is a string until resolved here.
Result.model_rebuild()


class Engine(ABC):
    """The base class every engine implements.

    A concrete engine sets :attr:`name` and :attr:`capabilities`, then implements
    :meth:`build` and :meth:`run`. :meth:`stream` and :meth:`resume` default to
    raising ``CapabilityError`` unless the engine declares (and overrides) them, so an
    engine can never silently pretend to do something it hasn't implemented.
    """

    #: Stable engine name used in ``AgentSpec.engine`` and the registry.
    name: ClassVar[str]
    #: What this engine supports; the kernel validates specs against it.
    capabilities: ClassVar[EngineCapabilities]

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Reject, at import, a concrete engine that forgot to declare ``capabilities``.

        The capability check needs every engine to declare what it supports, so a
        concrete engine missing that ClassVar is caught here — at import — with a clear
        message instead of an ``AttributeError`` later. Still-abstract subclasses are
        skipped (they're base classes, not shippable engines).
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
        """Compile a spec into a "compiled agent" only this engine's ``run``/``stream`` use.

        ``authored`` is an optional Python-authored agent object (carrying its own
        ``.spec``) produced by a ``code:`` reference. Core forwards it without looking
        inside; an engine that supports custom authoring (e.g. LangGraph's
        ``build_graph``) reads it, others ignore it and build from ``spec`` alone.
        ``None`` means a plain spec-only build.
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

    async def resume(
        self, compiled: Any, token: ResumeToken, ctx: RunContext, *, resume_value: Any = None
    ) -> Result:
        """Continue a paused/crashed run from a :class:`ResumeToken`.

        ``resume_value`` is the human's decision for a run that paused on a HITL
        ``interrupt`` (e.g. ``{"approved": True}``); it is ``None`` for a plain
        crash-resume. The default rejects a token minted by a different engine, and
        rejects resume on an engine that isn't durable — so nothing fakes a resume. A
        durable engine (LangGraph) overrides this to do the real replay. See DESIGN §3.1.
        """
        if token.engine != self.name:
            raise CapabilityError(
                f"resume token was minted by engine {token.engine!r} but this is "
                f"engine {self.name!r} — a token can only be resumed on the engine "
                f"that minted it"
            )
        self.capabilities.assert_supports("durability", "checkpoint")
        raise NotImplementedError(  # pragma: no cover - overridden by durable engines
            f"engine {self.name!r} declares durable execution but has not implemented resume yet"
        )


#: The single shared registry of engines, discovered via the entry-point group.
ENGINES: Registry[type[Engine]] = Registry("agentship.engines", label="engine")


def register_engine(cls: type[Engine]) -> type[Engine]:
    """Register an engine class under its :attr:`~Engine.name`. Usable as a decorator."""
    return ENGINES.register(cls.name, cls)


def assert_spec_supported(engine: Engine, spec: AgentSpec) -> None:
    """Check ``spec`` against ``engine``'s capabilities, raising on any mismatch.

    A free-function entry point (some callers, like the CLI, import it directly); it
    just delegates to :meth:`EngineCapabilities.assert_supports_spec`.
    """
    engine.capabilities.assert_supports_spec(spec)
