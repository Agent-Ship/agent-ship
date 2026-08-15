"""The authoring layer: :class:`AgentSpec` / :class:`MemberSpec` and their loaders.

An agent is declared as an :class:`AgentSpec` — the wiring the harness reads to
assemble it (which engine, model, prompt, members, and whether it streams). A
spec can be written in YAML (:func:`load_spec`) or authored in Python and handed
to :func:`resolve_code` via a ``code: "module:function"`` reference. Specs are
``extra="forbid"``: an unknown field is a loud error, never a silent typo.
"""

from __future__ import annotations

import importlib
import importlib.util
from collections.abc import Callable
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .errors import SpecError


class MemberSpec(BaseModel):
    """One member ("sub-agent") of a multi-agent team, declared in YAML.

    A member is a named worker a coordinator routes to. It is authored one of two ways:

    - by **reference** — ``ref`` points at the member's own agent YAML (the old-repo layout:
      a supervisor plus a folder of sub-agent YAMLs). The referenced file is a complete,
      independently-runnable agent; :func:`load_spec` resolves ``ref`` to an absolute path
      relative to the team YAML's directory.
    - **inline** — ``prompt`` (and optionally ``model``) define a simple single-model member
      right in the team YAML, no separate file.

    ``ref`` and ``prompt`` are mutually exclusive: a referenced sub-agent already brings its own
    prompt. ``description`` is an optional routing hint the coordinator's classifier uses to pick
    the right member (e.g. "billing, invoices, and payments").
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    #: Path to this member's own agent YAML (resolved to an absolute path at load time).
    #: Mutually exclusive with ``prompt``.
    ref: str | None = None
    #: A short hint describing what this member handles, used by the coordinator's classifier.
    description: str | None = None
    prompt: str | None = None
    model: str | None = None

    @model_validator(mode="after")
    def _check_member_coherence(self) -> MemberSpec:
        """Reject a member that sets both ``ref`` and inline ``prompt`` (contradictory)."""
        if self.ref is not None and self.prompt is not None:
            raise SpecError(
                f"member {self.name!r} sets both ref: {self.ref!r} and an inline prompt — a "
                f"referenced sub-agent already brings its own prompt. Use one or the other."
            )
        return self


class McpServerSpec(BaseModel):
    """One MCP server the agent connects to — the vendor-neutral half of the ``mcp:`` block.

    An MCP server is reached over one of two transports: **``stdio``** (a **local** server the
    client spawns as a subprocess — needs ``command`` + ``args``) or **``streamable_http``** (a
    **remote** server the client connects to over HTTP — needs ``url`` + optional ``headers``). Plain
    protocol config; the LangGraph adapter hands it to ``langchain-mcp-adapters``'
    ``MultiServerMCPClient`` (we do not hand-roll the client — see the P03 spec / memory).
    """

    model_config = ConfigDict(extra="forbid")

    transport: Literal["stdio", "streamable_http"]
    #: stdio: the executable to spawn (e.g. ``npx``) and its arguments.
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    #: streamable_http: the server endpoint and optional static headers (e.g. an auth bearer).
    url: str | None = None
    headers: dict[str, str] | None = None

    @model_validator(mode="after")
    def _check_transport_coherence(self) -> McpServerSpec:
        """Require the field the chosen transport needs: stdio→``command``, http→``url``."""
        if self.transport == "stdio" and not self.command:
            raise SpecError("stdio MCP server needs a `command` to spawn (nothing to launch)")
        if self.transport == "streamable_http" and not self.url:
            raise SpecError("streamable_http MCP server needs a `url` to connect to")
        return self


class ModelParams(BaseModel):
    """Generation params applied to the resolved model (the tuning layer).

    These are the knobs that tune how the model generates and where it is reached,
    threaded to the engine's model call so a spec can be tuned or pointed at a
    local / self-hosted server without touching code. Every field is optional; an
    omitted field keeps the model's own default. Unknown keys are rejected so a
    typo (e.g. ``top_p`` where the field is unsupported) fails loudly at load time.
    """

    model_config = ConfigDict(extra="forbid")

    #: Sampling temperature (higher = more random). ``None`` keeps the model default.
    temperature: float | None = None
    #: Maximum number of tokens to generate. ``None`` keeps the model default.
    max_tokens: int | None = None
    #: Base URL of an OpenAI-compatible endpoint (e.g. a local Ollama/vLLM server).
    #: ``None`` uses the provider's hosted endpoint.
    api_base: str | None = None
    #: Per-request timeout in seconds. ``None`` keeps the model default.
    timeout: float | None = None


class AgentSpec(BaseModel):
    """The declarative definition of an agent (the authoring/control layer).

    Every field here is wiring the harness reads to build the agent. Unknown keys
    are rejected so a typo fails loudly at load time rather than being ignored.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    engine: str = "echo"
    code: str | None = None  # optional Python-authored build fn: "module:function"
    #: Which pre-written build body the engine should generate for this agent.
    #: ``"single"`` (one model + tools, zero author code), ``"graph"`` (a fillable
    #: supervisor scaffold), or ``"deepagents"`` (a configured autonomous agent).
    #: ``None`` (default) means the engine's own default build path, or — when
    #: ``code:`` is set — a custom Python-authored body. ``template`` and ``code``
    #: are mutually exclusive: a template *is* a pre-written body, so pairing it
    #: with a custom one is contradictory (see :meth:`_check_coherence`).
    template: Literal["single", "graph", "deepagents"] | None = None
    #: Tool references this agent may call, each a ``"mcp:<server>"`` or
    #: ``"module:function"`` string. These are **parsed and validated only** here;
    #: actual tool *execution* (MCP resolution + guarded invocation) lands in Phase
    #: 03, so a tool listed today is accepted by the spec but not yet executed —
    #: the field exists now so a ``single``/``graph`` template can declare its tools
    #: and the capability gate can reason about tool use honestly. ``None`` means
    #: "no tools", distinct from an explicit empty list.
    tools: list[str] | None = None
    #: MCP servers this agent connects to, keyed by a local name. Each entry is an
    #: :class:`McpServerSpec` (a local ``stdio`` or remote ``streamable_http`` server); their tools
    #: are discovered via ``langchain-mcp-adapters`` and bound alongside native ``tools``.
    mcp: dict[str, McpServerSpec] | None = None
    model: str | None = None
    prompt: str | None = None
    #: Optional generation params (temperature/max_tokens/api_base/timeout) threaded
    #: to the resolved model. ``None`` means the model keeps all its own defaults.
    params: ModelParams | None = None
    members: list[MemberSpec] | None = None
    #: When true the agent asks to stream tokens; the capability gate rejects this
    #: at build time on an engine that does not declare streaming.
    streaming: bool = False
    #: First-class declared structured-output target (DESIGN §13.11), a
    #: ``"module:Model"`` reference resolved when the structured-output middleware
    #: lands. The gate rejects it on an engine whose ``structured_output`` is
    #: ``"none"``. Held as a reference string for now; optional.
    output_schema: str | None = None
    #: Requested durable-execution mode. ``"none"`` (default) needs nothing; the
    #: gate rejects ``"checkpoint"``/``"workflow"`` on an engine that declares
    #: ``durability="none"`` so a crash-recovery promise is never silently dropped.
    durability: Literal["none", "checkpoint", "workflow"] = "none"
    #: Runtime checkpoint-flush mode for a durable run — *distinct* from ``durability``
    #: above (which is whether/how the engine is durable). ``"exit"`` persists only at
    #: graph end (loses mid-run state), ``"async"`` (default) writes a checkpoint after
    #: each node without blocking, ``"sync"`` writes it *before* the next node starts
    #: (strongest crash guarantee — what a ``kill -9`` demo needs). The LangGraph
    #: engine passes this to ``.ainvoke(..., durability=…)``; it is inert when
    #: ``durability="none"``. Kept a separate key from ``durability`` so the capability
    #: and the flush mode never collide. See phase 02 §C4 / DESIGN §6.
    durability_mode: Literal["sync", "async", "exit"] = "async"

    # NOTE: ``memory`` / ``guardrails`` / ``auth`` blocks are deliberately absent
    # here. Per the grow-per-pillar rule they land with their own phases — memory
    # (P08), guardrails (P07), auth (P04) — each carrying the validation and the
    # engine wiring that makes the block real. Adding empty stubs now would be an
    # over-claim the capability gate could not honour, so they are left out.

    @model_validator(mode="after")
    def _check_coherence(self) -> AgentSpec:
        """Reject spec-field combinations that contradict each other (fail-fast).

        These are cross-field rules the per-field types cannot express, checked once
        at construction so an incoherent spec fails loudly at load time rather than
        as a confusing mid-build error. Provider/durability/streaming coherence is
        *not* re-checked here — those depend on the resolved engine's declared
        capabilities and are gated at build time by
        :meth:`~agentship.engines.base.EngineCapabilities.assert_supports_spec`.

        Rules enforced:

        - ``template`` and ``code`` are mutually exclusive — a ``template`` *is* a
          pre-written build body, so pairing it with a custom Python one is
          contradictory; one must win, never both silently.
        - ``template: "deepagents"`` requires ``engine: "langgraph"`` — the
          deepagents autonomous archetype is a LangGraph-only template, so asking
          for it on another engine can never be satisfied.
        """
        if self.template is not None and self.code is not None:
            raise SpecError(
                f"spec {self.name!r} sets both template: {self.template!r} and "
                f"code: {self.code!r} — a template is a pre-written build body, so it "
                f"cannot be combined with a custom code: body. Choose one."
            )
        if self.template == "deepagents" and self.engine != "langgraph":
            raise SpecError(
                f"template 'deepagents' is only available on engine 'langgraph', but "
                f"spec {self.name!r} sets engine: {self.engine!r} — use "
                f"engine: langgraph or a different template."
            )
        return self


def load_spec(path: str | Path) -> AgentSpec:
    """Load and validate a YAML agent spec file into an :class:`AgentSpec`.

    Reads the file with :func:`yaml.safe_load` (never ``load``), validates it, and
    raises :class:`SpecError` with an actionable message on a missing file,
    malformed YAML, a non-mapping document, or an invalid/unknown field.

    A member's ``ref`` (a path to its own sub-agent YAML) is resolved to an absolute path
    **relative to this spec file's directory**, so a supervisor + ``specialists/*.yaml`` layout
    works regardless of the caller's working directory.
    """
    p = Path(path)
    try:
        raw = yaml.safe_load(p.read_text())
    except FileNotFoundError as exc:
        raise SpecError(f"agent spec not found: {p}") from exc
    except yaml.YAMLError as exc:
        raise SpecError(f"invalid YAML in {p}: {exc}") from exc
    if not isinstance(raw, dict):
        raise SpecError(f"{p}: expected a YAML mapping, got {type(raw).__name__}")
    try:
        spec = AgentSpec(**raw)
    except ValidationError as exc:
        raise SpecError(f"invalid agent spec {p}: {exc}") from exc
    for member in spec.members or []:
        if member.ref is not None:
            member.ref = str((p.parent / member.ref).resolve())
    return spec


def resolve_code(ref: str) -> Callable[..., AgentSpec]:
    """Resolve a ``"module:function"`` code reference to the authoring callable.

    Python-authored specs point ``code:`` at an importable ``module:function``;
    calling that function returns an :class:`AgentSpec`. Two forms are supported:

    - ``"my.package.module:build"`` — an importable module attribute;
    - ``"path/to/file.py:build"``   — a callable in a Python file on disk.

    Raises :class:`SpecError` with an actionable message when the reference is
    malformed, the module/file cannot be loaded, or the attribute is missing.
    """
    module_ref, sep, attr = ref.partition(":")
    if not sep or not attr:
        raise SpecError(f"code must be 'module:function', got {ref!r}")
    module = (
        _load_module_from_file(module_ref)
        if module_ref.endswith(".py")
        else _import_module(module_ref)
    )
    try:
        return getattr(module, attr)
    except AttributeError as exc:
        raise SpecError(f"code {ref!r}: no attribute {attr!r} in {module_ref}") from exc


def _import_module(name: str):
    """Import a module by dotted name, raising :class:`SpecError` on failure."""
    try:
        return importlib.import_module(name)
    except ImportError as exc:
        raise SpecError(f"code module {name!r} could not be imported: {exc}") from exc


def _load_module_from_file(path: str):
    """Load a Python file as an anonymous module, raising :class:`SpecError` on failure."""
    file = Path(path)
    if not file.exists():
        raise SpecError(f"code file not found: {file}")
    spec = importlib.util.spec_from_file_location(f"_agentship_code_{file.stem}", file)
    if spec is None or spec.loader is None:
        raise SpecError(f"could not load code file: {file}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
