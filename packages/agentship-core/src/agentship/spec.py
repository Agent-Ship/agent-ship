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

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from .errors import SpecError


class MemberSpec(BaseModel):
    """One member ("sub-agent") of a multi-agent team, declared in YAML.

    A member is a named worker a coordinator can route to. Each may carry its own
    ``prompt`` and its own ``model`` (enabling a cheap model for one member and a
    strong one for another). Multi-agent coordination itself arrives in a later
    phase; the field exists now so the capability gate can honestly reject a team
    spec on an engine that cannot coordinate members.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    prompt: str | None = None
    model: str | None = None


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
    model: str | None = None
    prompt: str | None = None
    #: Optional generation params (temperature/max_tokens/api_base/timeout) threaded
    #: to the resolved model. ``None`` means the model keeps all its own defaults.
    params: ModelParams | None = None
    members: list[MemberSpec] | None = None
    #: When true the agent asks to stream tokens; the capability gate rejects this
    #: at build time on an engine that does not declare streaming.
    streaming: bool = False
    #: A declared output schema reference; the gate rejects it on an engine that
    #: does not declare structured output. Held as a reference string for now.
    output: str | None = None


def load_spec(path: str | Path) -> AgentSpec:
    """Load and validate a YAML agent spec file into an :class:`AgentSpec`.

    Reads the file with :func:`yaml.safe_load` (never ``load``), validates it, and
    raises :class:`SpecError` with an actionable message on a missing file,
    malformed YAML, a non-mapping document, or an invalid/unknown field.
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
        return AgentSpec(**raw)
    except ValidationError as exc:
        raise SpecError(f"invalid agent spec {p}: {exc}") from exc


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
