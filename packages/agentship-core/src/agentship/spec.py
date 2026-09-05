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
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .errors import SpecError


class A2aAuthSpec(BaseModel):
    """How to authenticate an *outbound* A2A call to a networked member (§C6, authoring side).

    Mirrors the runtime injector selection: ``bearer`` reads a static token from ``token_env``,
    ``oauth2`` runs client-credentials against ``token_url``, ``mtls`` presents a client cert. Only
    the fields the chosen ``type`` needs are set. Kept here (not in the optional ``agentship.a2a``
    subtree) so a spec still parses when the interop layer is absent; the resolver maps it across.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["none", "bearer", "oauth2", "mtls"] = "none"
    token_env: str | None = None
    token_url: str | None = None
    client_id_env: str | None = None
    client_secret_env: str | None = None
    cert_path: str | None = None
    key_path: str | None = None


class A2aRemoteSpec(BaseModel):
    """A networked member's location + credentials, declared under ``members[].a2a`` (§C3).

    ``url`` is the remote agent's A2A base URL; its Agent Card is fetched from ``url + card_path``.
    Presence of this block on a member means "resolve me over A2A, not in-process."
    """

    model_config = ConfigDict(extra="forbid")

    url: str
    card_path: str = "/.well-known/agent-card.json"
    auth: A2aAuthSpec = Field(default_factory=A2aAuthSpec)
    timeout_seconds: int = 60


class A2aOAuth2Spec(BaseModel):
    """OAuth2 client-credentials details to advertise when ``oauth2`` is an exposed scheme (§C4).

    Optional. When present, the Agent Card's ``oauth2`` scheme advertises this ``token_url`` (where
    a caller obtains a bearer token) and ``scopes`` (each accepted scope name → its description), so
    a client can self-serve credentials. Absent, the card still advertises ``oauth2`` with empty
    flows (spec-valid) — conformant but with no token endpoint for the client to discover.
    """

    model_config = ConfigDict(extra="forbid")

    token_url: str
    scopes: dict[str, str] = Field(default_factory=dict)


class A2aExposeSpec(BaseModel):
    """The ``a2a`` block on an :class:`AgentSpec`: whether/how to serve the agent over A2A (§C4).

    Absent ⇒ the agent is never exposed on the network (Layer 0 default). ``expose: true`` publishes
    the Agent Card and mounts the A2A JSON-RPC endpoint; ``security`` lists the scheme names the
    inbound router enforces *and* the card advertises (they are the same set, so they cannot drift).
    Default-deny: exposing with no ``security`` is rejected at load, so no agent is ever open.
    """

    model_config = ConfigDict(extra="forbid")

    expose: bool = False
    security: list[str] = Field(default_factory=list)
    oauth2: A2aOAuth2Spec | None = None

    @model_validator(mode="after")
    def _require_security_when_exposed(self) -> A2aExposeSpec:
        """An exposed agent must declare at least one security scheme (default-deny, §C6)."""
        if self.expose and not self.security:
            raise SpecError(
                "a2a.expose is true but no a2a.security scheme is declared — an exposed agent "
                "must name at least one scheme (e.g. security: [oauth2]) so it is never open. "
                "Add a2a.security or set expose: false."
            )
        return self


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
    #: When set, this member is a **networked** specialist reached over A2A rather than an
    #: in-process ``ref``/``prompt`` one. Mutually exclusive with both — a member is resolved
    #: exactly one way. The resolver (P05) turns this into an A2A client at run time.
    a2a: A2aRemoteSpec | None = None

    @model_validator(mode="after")
    def _check_member_coherence(self) -> MemberSpec:
        """Reject a member declared more than one way — exactly one of ref / prompt / a2a."""
        ways = sum(x is not None for x in (self.ref, self.prompt, self.a2a))
        if ways > 1:
            raise SpecError(
                f"member {self.name!r} must be declared exactly one way — a referenced ref, an "
                f"inline prompt, or a networked a2a block — but sets more than one. Choose one."
            )
        return self


class McpServerSpec(BaseModel):
    """One MCP server the agent connects to — the vendor-neutral half of the ``mcp:`` block.

    An MCP server is reached over one of two transports: **``stdio``** (a **local** server the
    client spawns as a subprocess — needs ``command`` + ``args``) or **``streamable_http``** (a
    **remote** server the client connects to over HTTP — needs ``url`` + optional ``headers``).
    Plain protocol config; the LangGraph adapter hands it to ``langchain-mcp-adapters``'
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
    #: How hard a reasoning model should think before answering. ``None`` keeps the
    #: provider's default (which for a non-reasoning model means: no thinking at all).
    #:
    #: One knob rather than a per-provider one, because LiteLLM already maps these four
    #: levels onto each provider's own scale — an Anthropic thinking budget, an OpenAI
    #: o-series effort, a Gemini thinking config. Re-deriving that mapping here would mean
    #: owning a table that changes every time a provider ships a model.
    #:
    #: Typed rather than a free string so ``reasoning_effort: hgih`` fails when the spec
    #: loads, not as a provider 400 in the middle of someone's turn.
    reasoning_effort: Literal["minimal", "low", "medium", "high"] | None = None


class ObservabilitySpec(BaseModel):
    """The ``observability`` block on an :class:`AgentSpec`: whether and how to trace the agent.

    Declarative and vendor-neutral — the kernel owns this authoring surface and never imports the
    OpenTelemetry adapter. ``provider: none`` (or an absent block) leaves the agent untraced (the
    no-op observer); ``provider: otel`` builds the OpenTelemetry observer that fans the span tree
    out to every backend named in ``exporters``. Secrets (API keys, endpoints) never live here —
    they come from the environment — so this block is safe to commit.

    The exporter *names* are validated by the observability adapter (which knows which backends it
    can build), not here, so the kernel stays ignorant of specific vendors.
    """

    model_config = ConfigDict(extra="forbid")

    #: Observer implementation: ``otel`` (default) builds the OpenTelemetry observer; ``none`` is
    #: the explicit off switch — a present block that still leaves the agent untraced.
    provider: Literal["otel", "none"] = "otel"
    #: Backends the span tree fans out to. Defaults to whatever ``AGENTSHIP_OTEL_EXPORTERS``
    #: names, and to none at all when it is unset — the tree is still built, it just ships
    #: nowhere, so an offline run never dials out. Naming exporters here overrides the
    #: environment for this one agent.
    exporters: list[str] = Field(
        default_factory=lambda: __import__(
            "agentship.observability.registry", fromlist=["default_exporters"]
        ).default_exporters()
    )
    #: PHI gate — when false (default), prompt/response content is never put on a span.
    capture_content: bool = False
    #: Fraction of traces kept, 0.0–1.0. A root's keep/drop decision is inherited by its children.
    sample_ratio: float = 1.0
    #: Opt-in for exporters that ship spans off-box (e.g. LangSmith); false blocks that egress.
    #: Defaults to ``AGENTSHIP_OTEL_ALLOW_SAAS`` so an operator consents once for the whole
    #: deployment instead of editing every spec; an explicit ``false`` here still wins.
    allow_saas_exporter: bool = Field(
        default_factory=lambda: __import__(
            "agentship.observability.registry", fromlist=["saas_egress_allowed"]
        ).saas_egress_allowed()
    )

    @field_validator("sample_ratio")
    @classmethod
    def _ratio_in_range(cls, value: float) -> float:
        """Reject a ratio outside 0.0–1.0 — a typo that would silently lose or flood traces."""
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"sample_ratio must be 0.0–1.0, got {value}")
        return value


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
    #: supervisor scaffold), or ``"autonomous"`` (a single self-directing agent that
    #: plans and calls its own tools in a loop — wraps the ``deepagents`` library).
    #: ``None`` (default) means the engine's own default build path, or — when
    #: ``code:`` is set — a custom Python-authored body. ``template`` and ``code``
    #: are mutually exclusive: a template *is* a pre-written body, so pairing it
    #: with a custom one is contradictory (see :meth:`_check_coherence`).
    template: Literal["single", "graph", "autonomous"] | None = None
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
    #: Skills (how-to guidance) this agent has — each a registered skill name or a path to a
    #: SKILL.md folder (the Agent Skills format). Their instructions augment the system prompt so
    #: the model knows *how* to use its tools/MCP. Distinct from ``tools`` (executable ones).
    skills: list[str] | None = None
    #: Optional allow-list of tool names to expose to the model. When set, only tools (native
    #: **or** MCP) whose name is listed are bound — the guard against many MCP servers flooding the
    #: model with hundreds of tools. ``None`` means expose every resolved tool.
    allowed_tools: list[str] | None = None
    #: When true, a **side-effecting** tool pauses for human approval (HITL) *before* it runs — the
    #: engine ``interrupt()``\\s with the pending write, and the run resumes only on an
    #: ``{"approved": true}`` decision. Requires a durable run (``durability: checkpoint``).
    confirm_writes: bool = False
    model: str | None = None
    prompt: str | None = None
    #: Optional generation params (temperature/max_tokens/api_base/timeout) threaded
    #: to the resolved model. ``None`` means the model keeps all its own defaults.
    params: ModelParams | None = None
    members: list[MemberSpec] | None = None
    #: Optional A2A exposure block. Absent (default) ⇒ the agent is never served on the network;
    #: ``a2a.expose: true`` publishes its Agent Card and mounts the A2A endpoint (P05 · C4). Layer 0
    #: agents omit it entirely and never accidentally reach the wire.
    a2a: A2aExposeSpec | None = None
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
    #: How (and whether) to trace this agent. Absent → untraced (no-op observer); see
    #: :class:`ObservabilitySpec`. Secrets stay in the environment, so this block is safe to commit.
    observability: ObservabilitySpec | None = None
    durability: Literal["none", "checkpoint", "workflow"] = "none"
    # NOTE: the *runtime checkpoint-flush mode* (LangGraph's ``ainvoke(durability=…)``:
    # sync/async/exit) is deliberately NOT a spec field. It is an engine implementation
    # detail — a user declaring ``durability: checkpoint`` is asking for crash safety, not
    # for a flush-timing knob — so the LangGraph engine picks the strongest flush mode
    # internally (``sync``) and never leaks the vendor's vocabulary into portable YAML.

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
        - ``template: "autonomous"`` requires ``engine: "langgraph"`` — the
          autonomous single-agent archetype is a LangGraph-only template, so asking
          for it on another engine can never be satisfied.
        """
        if self.template is not None and self.code is not None:
            raise SpecError(
                f"spec {self.name!r} sets both template: {self.template!r} and "
                f"code: {self.code!r} — a template is a pre-written build body, so it "
                f"cannot be combined with a custom code: body. Choose one."
            )
        if self.template == "autonomous" and self.engine != "langgraph":
            raise SpecError(
                f"template 'autonomous' is only available on engine 'langgraph', but "
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
    # Register the module in ``sys.modules`` *before* executing it — the importlib-recommended
    # pattern. Without this, ``typing.get_type_hints`` (called by LangGraph on a graph's
    # ``TypedDict`` state) cannot find the module's globals to resolve string forward refs like
    # ``Annotated[list, add_messages]``, and fails with ``NameError`` for a custom agent authored
    # via a file-path ``code:`` ref. Registering it also lets the module's dataclasses/enums pickle.
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(spec.name, None)  # don't leave a half-initialised module registered
        raise
    return module
