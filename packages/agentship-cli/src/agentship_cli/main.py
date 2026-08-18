"""The ``agentship`` command-line interface.

A thin wrapper over the harness. It offers:

- ``agentship run <file> --input ... [--stream]`` — load a YAML spec, build the
  agent, run (or stream) one turn, and print the output.
- ``agentship doctor [--agents-dir DIR | FILE]`` — validate agent specs against
  their engines' declared capabilities, with actionable install hints when an
  engine's package is not installed.
- ``agentship init [DIR]`` — scaffold a new single-tenant project.
- ``agentship new-agent NAME`` — scaffold one starter agent spec.
- ``agentship db upgrade [--allow-migrations]`` — the single, gated owning
  entry point for all schema migrations (DDL). Plan-only by default; only
  ``--allow-migrations`` may apply anything (§13.9).

Harness errors are caught and printed as a clean message with a non-zero exit
code rather than a traceback.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
from pathlib import Path

import click
from agentship.engines.base import ENGINES, assert_spec_supported
from agentship.errors import AgentShipError
from agentship.logs import configure_logging
from agentship.runtime import build_agent
from agentship.spec import load_spec

from . import scaffold
from .migrations import REGISTERED_MIGRATIONS, Migration

#: Environment variables consulted (in order) for the database DSN when
#: ``--database-url`` is not given. ``AGENTSHIP_DATABASE_URL`` is preferred; a
#: generic ``DATABASE_URL`` is accepted as a fallback for common deployments.
DATABASE_URL_ENV_VARS = ("AGENTSHIP_DATABASE_URL", "DATABASE_URL")

#: Known engine name → the ``pip install`` target that provides it. Used by
#: ``doctor`` to turn "engine not installed" into an actionable fix instead of an
#: opaque lookup miss. An engine not in this map gets a generic hint naming the
#: conventional ``agentship-<name>`` package.
ENGINE_PIP_TARGET = {
    "langgraph": "agentship[langgraph]",
    "pydantic-ai": "agentship-pydantic-ai",
    "pydantic_ai": "agentship-pydantic-ai",
    "adk": "agentship-adk",
}


def _install_hint(engine: str) -> str:
    """Return the ``pip install`` command that provides ``engine``.

    Looks the engine up in :data:`ENGINE_PIP_TARGET`; falls back to the
    conventional ``agentship-<engine>`` package name for an unknown engine so the
    message is always actionable rather than an opaque lookup miss.
    """
    target = ENGINE_PIP_TARGET.get(engine, f"agentship-{engine}")
    return f"pip install {target}"


def load_env_for_run(env_file: str | None) -> None:
    """Load provider credentials from a ``.env`` for a real ``agentship run``.

    Reads environment variables from a ``.env`` file so a user can supply a key
    like ``OPENAI_API_KEY`` without exporting it by hand. This is called only from
    inside a command function — never at import time — so importing ``agentship``
    or running the test suite never pulls a ``.env`` into the environment (which is
    what once caused tests to fire stray paid provider calls).

    When ``env_file`` is given it must exist: a missing path raises
    :class:`~agentship.errors.AgentShipError` so the CLI reports a clean ``Error:``
    rather than silently ignoring a typo. When it is ``None`` the current
    directory's ``.env`` is loaded if present (and skipped silently if absent).

    Loading uses ``override=False`` so a variable already exported in the real
    environment always wins over the ``.env`` value.
    """
    from dotenv import load_dotenv

    if env_file is not None:
        path = Path(env_file)
        if not path.is_file():
            raise AgentShipError(f"--env-file not found: {env_file}")
        load_dotenv(dotenv_path=path, override=False)
        return

    default = Path.cwd() / ".env"
    if default.is_file():
        load_dotenv(dotenv_path=default, override=False)


@click.group()
def main() -> None:
    """AgentShip — run agents authored in YAML or Python."""


@main.command()
@click.argument("file", type=click.Path(exists=True, dir_okay=False))
@click.option("--input", "input_text", required=True, help="The input text for one turn.")
@click.option("--stream", is_flag=True, help="Stream the response as events instead of one result.")
@click.option(
    "--env-file",
    "env_file",
    default=None,
    help="Load environment variables from this .env file (defaults to ./.env if present).",
)
@click.option("--debug", is_flag=True, help="Re-raise on failure so the full traceback is shown.")
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    help="Show the agent's internal decision logs at INFO (routing, tool calls, checkpoints).",
)
def run(
    file: str,
    input_text: str,
    stream: bool,
    env_file: str | None,
    debug: bool,
    verbose: bool,
) -> None:
    """Run one turn of the agent declared in FILE and print its output.

    Before running, environment variables are loaded from a ``.env`` — the current
    directory's ``.env`` by default, or the file named by ``--env-file`` — so a
    provider key such as ``OPENAI_API_KEY`` need not be exported by hand. An
    already-exported variable is never overwritten by the ``.env``.

    With ``--verbose`` the agent's internal decision log is printed to stderr at INFO
    level — colorized and leveled — covering engine build, MCP discovery, tool calls,
    checkpoint/resume, and (for a supervisor) the classify → route → dispatch → resolve
    trace. ``--debug`` raises the log level to DEBUG (and re-raises on failure for the
    full traceback). Only stdout carries the final answer, so neither flag pollutes a
    piped result.

    On failure the CLI prints a single clean ``Error: …`` line to stderr and exits
    ``1`` — never a raw traceback. Known harness failures
    (:class:`~agentship.errors.AgentShipError` and its subclasses, e.g.
    ``ModelError`` / ``SpecError`` / ``CapabilityError``) print their actionable
    message as-is. Any unexpected error prints its concise message plus a hint to
    re-run with ``--debug``. With ``--debug`` set, the original exception is
    re-raised so the full traceback surfaces for diagnosis.
    """
    try:
        load_env_for_run(env_file)
        if debug:
            configure_logging(logging.DEBUG)
        elif verbose:
            configure_logging(logging.INFO)
        agent = build_agent(file)
        if stream:
            asyncio.run(_stream_turn(agent, input_text))
        else:
            result = asyncio.run(agent.run(input_text))
            click.echo(result.output)
    except AgentShipError as exc:
        # Expected harness failure: already carries an actionable message.
        if debug:
            raise
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)
    except Exception as exc:
        # Unexpected failure: show a concise message, not a wall of traceback,
        # and point at --debug for the full trace.
        if debug:
            raise
        click.echo(
            f"Error: {exc} (run with --debug for the full traceback)", err=True
        )
        sys.exit(1)


async def _stream_turn(agent, input_text: str) -> None:
    """Drive the agent's stream, printing each content chunk as it arrives.

    Content events are printed inline; the terminal ``done`` event ends the line.
    """
    async for event in agent.stream(input_text):
        if event.type == "content" and event.data is not None:
            click.echo(event.data, nl=False)
        elif event.type == "done":
            click.echo()  # newline terminating the streamed line


def _check_agent(path: Path) -> str | None:
    """Validate one agent spec; return an error reason string, or ``None`` if OK.

    Loads the YAML into an :class:`~agentship.spec.AgentSpec`, resolves its engine
    from the :data:`~agentship.engines.base.ENGINES` registry, and runs the
    capability gate (:func:`~agentship.engines.base.assert_spec_supported`). Every
    expected failure is turned into a clean one-line reason (never a traceback):

    - a missing file / malformed YAML / unknown field raises ``SpecError`` →
      its actionable message;
    - an engine whose package is not installed → a reason naming the exact
      ``pip install`` fix (via :func:`_install_hint`), not an opaque lookup miss;
    - a capability mismatch raises ``CapabilityError`` → its actionable message.

    Any :class:`~agentship.errors.AgentShipError` is caught here so the caller can
    print a status line; unexpected errors propagate to be handled once at the
    command level (and re-raised under ``--debug``).
    """
    spec = load_spec(path)  # SpecError on bad YAML / unknown field — caught by caller
    if spec.engine not in ENGINES:
        return (
            f"engine {spec.engine!r} not installed — {_install_hint(spec.engine)} "
            f"(installed: {ENGINES.names()})"
        )
    engine = ENGINES.get(spec.engine)()
    assert_spec_supported(engine, spec)  # CapabilityError on mismatch — caught by caller
    autonomous_reason = _check_autonomous_version(spec)
    if autonomous_reason is not None:
        return autonomous_reason
    mcp_reason = _check_mcp_version(spec)
    if mcp_reason is not None:
        return mcp_reason
    return None


def _check_mcp_version(spec) -> str | None:
    """Guard an agent that declares ``mcp:`` servers against a missing/out-of-range ``mcp`` SDK.

    Returns an actionable reason when the ``[mcp]`` extra is not installed or the installed ``mcp``
    version is outside the supported ``>=1.28,<2`` range, or ``None`` when the spec has no ``mcp:``
    servers or the install is fine. Never raises — if the langgraph adapter is not importable here
    the guard is simply skipped.
    """
    if not getattr(spec, "mcp", None):
        return None
    try:
        from agentship_langgraph.mcp import mcp_version_ok
    except ImportError:
        return None
    ok, installed = mcp_version_ok()
    if ok:
        return None
    if installed is None:
        return "this agent declares mcp: servers — pip install 'agentship-langgraph[mcp]'"
    return (
        f"mcp {installed} is installed but agentship needs mcp>=1.28,<2 "
        f"(v2 reshaped the client API) — pip install 'mcp>=1.28,<2'"
    )


def _check_autonomous_version(spec) -> str | None:
    """Guard a ``template: autonomous`` spec against a missing/drifted deepagents install.

    The ``autonomous`` template wraps the deepagents library, which is pre-1.0 (its
    ``create_deep_agent`` signature can drift), so an ``autonomous`` spec is only
    healthy when the pinned version is installed. This reads the langgraph adapter's
    version guard *if that adapter is importable* (``doctor`` runs in projects that
    may not have it), returning an actionable reason when deepagents is absent or the
    wrong version, or ``None`` when the spec is not an autonomous one or the install
    is fine. Never raises: an import failure just means the guard is skipped (the
    capability gate already vouched for the engine).
    """
    if getattr(spec, "template", None) != "autonomous":
        return None
    try:
        from agentship_langgraph.templates.autonomous_tpl import (
            PINNED_DEEPAGENTS_VERSION,
            deepagents_version_ok,
        )
    except ImportError:
        return None  # langgraph adapter not importable here — skip the extra guard
    ok, installed = deepagents_version_ok()
    if ok:
        return None
    if installed is None:
        return (
            "template 'autonomous' needs the deepagents package — "
            "pip install 'agentship-langgraph[autonomous]'"
        )
    return (
        f"template 'autonomous' is pinned to deepagents=={PINNED_DEEPAGENTS_VERSION} "
        f"but {installed} is installed — pip install "
        f"'deepagents=={PINNED_DEEPAGENTS_VERSION}' (pre-1.0 API can drift)"
    )


def _agent_files(agents_dir: Path) -> list[Path]:
    """Return the sorted ``*.yaml``/``*.yml`` files directly under ``agents_dir``.

    Only the directory's own specs are listed (not a deep walk), so a nested
    Python package or fixtures folder is never mistaken for an agent spec.
    """
    files = sorted(p for p in agents_dir.iterdir() if p.suffix in (".yaml", ".yml"))
    return files


@main.command()
@click.argument("target", type=click.Path(exists=True), required=False)
@click.option(
    "--agents-dir",
    "agents_dir",
    type=click.Path(exists=True, file_okay=False),
    default=None,
    help="Validate every *.yaml agent in this directory (default: ./agents).",
)
@click.option(
    "--debug", is_flag=True, help="Re-raise on an unexpected failure for the full traceback."
)
def doctor(target: str | None, agents_dir: str | None, debug: bool) -> None:
    """Validate agent specs against their engines' declared capabilities.

    Give either a single spec FILE or ``--agents-dir DIR`` (default ``./agents``).
    For each agent this loads the YAML, resolves its ``engine`` from the registry,
    and runs the capability gate. It prints a per-agent status line — ``OK`` or
    ``✗`` with the reason — and exits ``1`` if *any* agent is invalid, ``0`` when
    all pass.

    Every expected failure is a clean status/``Error:`` line, never a traceback:
    a bad YAML, an unknown field, an engine whose package is not installed (the
    reason names the exact ``pip install`` fix), or a capability mismatch. Pass
    ``--debug`` to re-raise an *unexpected* error with its full traceback.
    """
    try:
        files = _resolve_doctor_targets(target, agents_dir)
    except AgentShipError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)

    any_bad = False
    for path in files:
        try:
            reason = _check_agent(path)
        except AgentShipError as exc:
            # Expected harness failure (bad spec / capability mismatch): show it as
            # this agent's reason, keep checking the rest.
            reason = str(exc)
        except Exception as exc:  # noqa: BLE001 - unexpected; surface cleanly or re-raise
            if debug:
                raise
            reason = f"{exc} (run with --debug for the full traceback)"
        if reason is None:
            click.echo(f"OK   {path.name}")
        else:
            any_bad = True
            click.echo(f"✗    {path.name}: {reason}")

    if any_bad:
        sys.exit(1)


def _resolve_doctor_targets(target: str | None, agents_dir: str | None) -> list[Path]:
    """Resolve the doctor command's inputs to a non-empty list of spec files.

    Precedence: an explicit ``target`` file/dir wins; otherwise ``--agents-dir``;
    otherwise the default ``./agents`` directory. Raises
    :class:`~agentship.errors.AgentShipError` with an actionable message when the
    default ``./agents`` is missing or when a chosen directory holds no specs, so
    the caller prints one clean ``Error:`` line rather than silently passing.
    """
    if target is not None:
        path = Path(target)
        if path.is_dir():
            return _require_specs(path)
        return [path]
    if agents_dir is not None:
        return _require_specs(Path(agents_dir))
    default = Path("agents")
    if not default.is_dir():
        raise AgentShipError(
            "no agents to check — pass a spec FILE, use --agents-dir DIR, or run "
            "from a project with an ./agents directory (see `agentship init`)"
        )
    return _require_specs(default)


def _require_specs(agents_dir: Path) -> list[Path]:
    """Return the specs under ``agents_dir`` or raise when there are none."""
    files = _agent_files(agents_dir)
    if not files:
        raise AgentShipError(f"no *.yaml agent specs found in {agents_dir}")
    return files


def _write_new_file(path: Path, text: str, *, force: bool = False) -> None:
    """Write ``text`` to ``path``, refusing to overwrite unless ``force`` is set.

    Scaffolding never clobbers a user's work by default: an existing target raises
    :class:`~agentship.errors.AgentShipError` so the caller reports a clean error
    instead of silently replacing content. Passing ``force=True`` (from
    ``--force``) allows the overwrite — for re-scaffolding an agent on purpose.
    Parent directories are created first.
    """
    if path.exists() and not force:
        raise AgentShipError(
            f"refusing to overwrite existing file: {path} (pass --force to replace it)"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


#: The agent-name pattern the spec's registry key must match: a lowercase
#: identifier (letter first, then letters/digits/underscore). Validated by
#: ``new-agent`` *before* any file is written so a bad name fails fast and cleanly
#: rather than producing a spec that later fails to load or a broken Python
#: class name in a ``graph`` scaffold.
_AGENT_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


def _validate_agent_name(name: str) -> None:
    """Raise :class:`AgentShipError` if ``name`` is not a valid agent identifier.

    Keeps scaffolding total: the name becomes the spec's ``name``, the YAML file
    stem, and (for ``graph``) a Python class prefix, so it must be a plain
    lowercase identifier. An invalid name is refused before any file is touched.
    """
    if not _AGENT_NAME_PATTERN.match(name):
        raise AgentShipError(
            f"invalid agent name {name!r} — use lowercase letters, digits and "
            f"underscores, starting with a letter (e.g. 'ticket_router')"
        )


@main.command()
@click.argument("directory", type=click.Path(file_okay=False), default=".")
def init(directory: str) -> None:
    """Scaffold a new single-tenant AgentShip project in DIRECTORY (default ``.``).

    Creates an ``agents/`` folder with a starter ``assistant.yaml`` (the default
    ``langgraph`` engine over ``openai/gpt-4o-mini``), a ``.env.example`` naming the
    one key the quickstart needs, and a ``README.md`` showing the ``agentship run``
    path. The scaffold is single-tenant — no auth or tenancy concepts — so a fresh
    project just runs.

    Existing files are never overwritten: if any target already exists the command
    reports a clean ``Error:`` and exits ``1`` without touching your files.
    """
    root = Path(directory)
    try:
        _write_new_file(root / "agents" / "assistant.yaml", scaffold.ASSISTANT_YAML)
        _write_new_file(root / ".env.example", scaffold.ENV_EXAMPLE)
        _write_new_file(root / "README.md", scaffold.README)
    except AgentShipError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)

    click.echo(f"Scaffolded an AgentShip project in {root}/")
    click.echo("Next: cp .env.example .env  # set OPENAI_API_KEY")
    click.echo('      agentship run agents/assistant.yaml --input "hello"')


#: The templates ``new-agent`` can scaffold. ``single`` and ``autonomous`` are
#: pure-YAML (the langgraph engine ships their build body); ``graph`` is a
#: custom-authoring scaffold that also writes a companion ``agent.py``.
_TEMPLATE_CHOICES = ("single", "graph", "autonomous")


@main.command(name="new-agent")
@click.argument("name")
@click.option(
    "--template",
    type=click.Choice(_TEMPLATE_CHOICES),
    default="single",
    show_default=True,
    help="Which agent template to scaffold.",
)
@click.option(
    "--agents-dir",
    "agents_dir",
    type=click.Path(file_okay=False),
    default="agents",
    show_default=True,
    help="Directory to write the agent spec into.",
)
@click.option(
    "--force",
    is_flag=True,
    help="Overwrite existing scaffold files instead of refusing.",
)
def new_agent(name: str, template: str, agents_dir: str, force: bool) -> None:
    """Scaffold one starter agent from a template at ``<agents-dir>/NAME.yaml``.

    ``--template single`` (default) and ``--template autonomous`` write a single
    pure-YAML spec — no companion Python — that the ``langgraph`` engine turns into
    a runnable agent from the YAML alone. ``--template graph`` writes both
    ``NAME.yaml`` and a companion ``NAME/agent.py``: a fillable
    :class:`~agentship_langgraph.agent.LangGraphAgent` supervisor scaffold whose
    ``build_graph`` carries ``# TODO(author)`` markers, with the YAML's ``code:``
    pointing at it (written as an absolute path so the spec builds regardless of the
    working directory it is loaded from).

    ``NAME`` is validated against the agent-name pattern before any file is written,
    so a bad name fails fast and cleanly. Existing files are never clobbered: the
    command reports a clean ``Error:`` and exits ``1`` unless ``--force`` is passed.
    """
    try:
        _validate_agent_name(name)
        written = _scaffold_agent(name, template, Path(agents_dir), force=force)
    except AgentShipError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)
    for path in written:
        click.echo(f"Wrote {path}")
    yaml_path = Path(agents_dir) / f"{name}.yaml"
    click.echo(f'Run it: agentship run {yaml_path} --input "hello"')


def _scaffold_agent(name: str, template: str, agents_dir: Path, *, force: bool) -> list[Path]:
    """Write the files for ``template`` and return the paths written (YAML first).

    Dispatches on ``template``: ``single``/``autonomous`` write one pure-YAML spec;
    ``graph`` writes ``NAME.yaml`` plus a companion ``NAME/agent.py`` supervisor
    scaffold, with the YAML's ``code:`` referencing the ``agent.py`` by its absolute
    path so :func:`agentship.spec.resolve_code` finds it from any working directory.
    Refuses to overwrite an existing file unless ``force`` is set (via
    :func:`_write_new_file`). An unknown ``template`` is impossible here — Click's
    ``Choice`` rejects it at parse time — but is still guarded so a future caller
    gets a clean error rather than a silent miss.
    """
    yaml_path = agents_dir / f"{name}.yaml"
    if template == "single":
        _write_new_file(yaml_path, scaffold.single_template_yaml(name), force=force)
        return [yaml_path]
    if template == "autonomous":
        _write_new_file(yaml_path, scaffold.autonomous_template_yaml(name), force=force)
        return [yaml_path]
    if template == "graph":
        agent_py = (agents_dir / name / "agent.py").resolve()
        code_ref = f"{agent_py}:build_agent"
        # Write the companion agent.py first so a mid-way failure never leaves a
        # YAML whose code: points at a missing file.
        _write_new_file(agent_py, scaffold.graph_template_agent_py(name), force=force)
        _write_new_file(yaml_path, scaffold.graph_template_yaml(name, code_ref), force=force)
        return [yaml_path, agent_py]
    raise AgentShipError(f"unknown template {template!r} — choose one of {_TEMPLATE_CHOICES}")


@main.command()
@click.option("--host", default="127.0.0.1", show_default=True, help="Interface to bind.")
@click.option("--port", default=8000, show_default=True, type=int, help="Port to bind.")
@click.option(
    "--reload",
    is_flag=True,
    help="Auto-reload on code changes (dev; mutually exclusive with --workers>1).",
)
@click.option(
    "--workers",
    type=int,
    default=None,
    help="Number of worker processes (prod; mutually exclusive with --reload).",
)
@click.option(
    "--agents-dir",
    "agents_dir",
    type=click.Path(file_okay=False),
    default="agents",
    show_default=True,
    help="Directory of agent specs to serve.",
)
@click.option(
    "--auth",
    "auth_provider",
    default="api_key",
    show_default=True,
    help="Auth provider name (api_key | forwarded | jwt | composite).",
)
@click.option(
    "--env-file",
    default=None,
    help="Load provider credentials from this .env before serving (default: ./.env).",
)
def serve(
    host: str,
    port: int,
    reload: bool,
    workers: int | None,
    agents_dir: str,
    auth_provider: str,
    env_file: str | None,
) -> None:
    """Serve the agents in AGENTS-DIR over the secure ``/v1`` REST/SSE/WS surface.

    This is the supported launch path for the runtime service. It is **doctor-gated**: every
    ``agents/*.yaml`` is validated and the auth provider is built *before* the socket binds,
    so an invalid spec, an uninstalled engine, or an uninstalled/misconfigured auth provider
    fails fast (exit ``1``) rather than after the server is already listening. The app is
    then built by the same ``create_app()`` factory tests use and run under uvicorn.

    ``--host`` defaults to loopback (``127.0.0.1``) so a bare ``agentship serve`` is not
    network-exposed. ``--reload`` (dev) and ``--workers>1`` (prod) are mutually exclusive —
    a uvicorn constraint — and passing both is a usage error (exit ``2``).
    """
    if reload and workers and workers > 1:
        raise click.UsageError(
            "--reload and --workers>1 are mutually exclusive (uvicorn constraint)"
        )
    try:
        _serve(host, port, reload, workers, Path(agents_dir), auth_provider, env_file)
    except AgentShipError as exc:
        # Doctor-gate failure (bad spec / uninstalled or misconfigured provider): exit 1
        # before anything is bound.
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


def _serve(
    host: str,
    port: int,
    reload: bool,
    workers: int | None,
    agents_dir: Path,
    auth_provider: str,
    env_file: str | None,
) -> None:
    """Doctor-gate, configure the app factory's environment, and launch the server.

    Raises :class:`~agentship.errors.AgentShipError` (→ the command exits 1) if any spec is
    invalid or the auth provider cannot be built; only past that gate does it hand off to
    :func:`run_server`, which binds the socket.
    """
    from agentship.auth.registry import build_auth_provider
    from agentship_service.serving import (
        ENV_AGENTS_DIR,
        ENV_AUTH_PROVIDER,
        _auth_config_from_env,
    )

    load_env_for_run(env_file)

    # Doctor-gate: validate every spec before binding, with actionable per-agent reasons.
    specs = _agent_files(agents_dir) if agents_dir.is_dir() else []
    failures = [(path.name, _check_agent(path)) for path in specs]
    failures = [(name, reason) for name, reason in failures if reason is not None]
    if failures:
        for name, reason in failures:
            click.echo(f"✗ {name}: {reason}", err=True)
        raise AgentShipError(
            f"doctor gate failed: {len(failures)} invalid agent spec(s) — fix before serving"
        )

    # Configure the factory's environment, then build the provider once to fail fast on an
    # uninstalled or misconfigured provider (e.g. forwarded-header without an allow-list).
    os.environ[ENV_AGENTS_DIR] = str(agents_dir)
    os.environ[ENV_AUTH_PROVIDER] = auth_provider
    build_auth_provider(auth_provider, _auth_config_from_env(auth_provider))

    click.echo(f"Serving {len(specs)} agent(s) from {agents_dir} on http://{host}:{port}")
    click.echo(f"Auth provider: {auth_provider}")
    run_server(host=host, port=port, reload=reload, workers=workers)


def run_server(*, host: str, port: int, reload: bool, workers: int | None) -> None:
    """Launch uvicorn against the env-configured app factory (indirected for testability).

    Uses the import-string factory ``agentship_service.serving:build_from_env`` so uvicorn
    can rebuild the app in ``--reload``/``--workers`` subprocesses from the environment the
    doctor-gate already configured. Tests monkeypatch this function to avoid binding a socket.
    """
    import uvicorn

    uvicorn.run(
        "agentship_service.serving:build_from_env",
        factory=True,
        host=host,
        port=port,
        reload=reload,
        workers=workers or None,
    )


#: Hosts treated as loopback — the only interfaces ``agentship studio`` may bind (§13.8).
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


@main.command()
@click.option(
    "--engine",
    type=click.Choice(["langgraph", "adk"]),
    default="langgraph",
    show_default=True,
    help="Which consumed studio UI to open (LangGraph Studio or ADK web).",
)
@click.option(
    "--host",
    default="127.0.0.1",
    show_default=True,
    help="Interface to bind — loopback only (127.0.0.1 | localhost | ::1).",
)
@click.option("--port", default=2024, show_default=True, type=int, help="Studio dev-server port.")
@click.option(
    "--agents-dir",
    "agents_dir",
    type=click.Path(file_okay=False),
    default="agents",
    show_default=True,
    help="Directory of agent specs to open in the studio.",
)
@click.option(
    "--env-file",
    default=None,
    help="Load credentials from this .env before launching (default: ./.env).",
)
def studio(engine: str, host: str, port: int, agents_dir: str, env_file: str | None) -> None:
    """Open a consumed studio UI (LangGraph Studio / ADK web) against AGENTS-DIR.

    We do **not** build a studio: for ``--engine langgraph`` we generate a ``langgraph.json``
    manifest from the discovered LangGraph agents and shell to ``langgraph dev``; for
    ``--engine adk`` we shell to ADK web. Phoenix remains the trace/eval surface.

    **Loopback-only (§13.8, non-negotiable):** the studio binds ``127.0.0.1`` with a minted dev
    token and a ``dev`` principal, and **refuses any non-loopback host outright**. Studio
    time-travel reads checkpoints, so exposing it on a network interface without an ``AuthProvider``
    is a hard cross-tenant leak — expose it only behind the secure service.
    """
    try:
        _studio(engine, host, port, Path(agents_dir), env_file)
    except AgentShipError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


def _require_loopback(host: str) -> None:
    """Raise unless ``host`` is a loopback interface — the studio's hard §13.8 guard."""
    if host not in LOOPBACK_HOSTS:
        raise AgentShipError(
            f"studio binds loopback only (§13.8); refusing host {host!r}. Studio time-travel reads "
            "checkpoints — put it behind an AuthProvider to expose it on a network interface."
        )


def _studio(engine: str, host: str, port: int, agents_dir: Path, env_file: str | None) -> None:
    """Enforce the loopback guard, mint a dev token, generate the manifest, and launch the UI.

    Raises :class:`~agentship.errors.AgentShipError` (→ exit 1) on a non-loopback host or when no
    renderable agent is found; only past those gates does it hand off to :func:`run_studio_process`,
    which execs the consumed dev server.
    """
    import secrets

    _require_loopback(host)
    load_env_for_run(env_file)

    # Mint a dev token → dev principal for this loopback session (reuse one already in the env).
    dev_token = os.environ.get("AGENTSHIP_DEV_TOKEN") or secrets.token_urlsafe(24)
    env = {**os.environ, "AGENTSHIP_DEV_TOKEN": dev_token, "AGENTSHIP_PRINCIPAL": "dev"}

    if engine == "langgraph":
        from agentship.observability.studio import generate_langgraph_json

        manifest = generate_langgraph_json(agents_dir, Path("langgraph.json").resolve())
        click.echo(f"Wrote {manifest}")
        cmd = ["langgraph", "dev", "--host", host, "--port", str(port)]
        cwd = manifest.parent
    else:
        cmd = ["adk", "web", "--host", host, "--port", str(port), str(agents_dir.resolve())]
        cwd = agents_dir.parent

    click.echo(f"Dev token (loopback only): {dev_token}")
    click.echo(f"Opening {engine} studio on http://{host}:{port}")
    run_studio_process(cmd, env, cwd=cwd)


def run_studio_process(cmd: list[str], env: dict[str, str], *, cwd: Path) -> None:
    """Exec the consumed studio dev server (indirected so tests can avoid launching it).

    Runs ``langgraph dev`` / ``adk web`` in ``cwd`` with the dev-token environment. Tests
    monkeypatch this to assert the command and loopback host without spawning a process.
    """
    import subprocess

    subprocess.run(cmd, env=env, cwd=str(cwd), check=True)


@main.group()
def db() -> None:
    """Database schema commands (the gated owner of all DDL)."""


def _pending_migrations() -> list[Migration]:
    """Return the registered migrations sorted by ``version`` (apply order).

    This is the full plan for a fresh database. Because every migration is
    idempotent, applying already-present ones is a safe no-op, so the runner
    does not need to track applied state to be correct today. Later phases that
    add real DDL may add a version ledger; the KISS Week-1 runner does not.
    """
    return sorted(REGISTERED_MIGRATIONS, key=lambda m: m.version)


def _resolve_database_url(database_url: str | None) -> str | None:
    """Resolve the database DSN from the flag, then the environment.

    Precedence: an explicit ``--database-url`` wins; otherwise the first set of
    :data:`DATABASE_URL_ENV_VARS`. Returns ``None`` when no DSN is available so
    the caller can decide whether that is fatal (it is only fatal when there are
    pending migrations to apply).
    """
    if database_url:
        return database_url
    for name in DATABASE_URL_ENV_VARS:
        value = os.environ.get(name)
        if value:
            return value
    return None


def _print_plan(migrations: list[Migration]) -> None:
    """Print the human-readable upgrade plan (what *would* run)."""
    if not migrations:
        click.echo("No migrations registered — nothing to apply.")
        return
    click.echo(f"{len(migrations)} migration(s) would be applied:")
    for migration in migrations:
        click.echo(f"  {migration.version}  {migration.description}")


@db.command()
@click.option(
    "--database-url",
    "database_url",
    default=None,
    help="Database DSN to migrate (falls back to $AGENTSHIP_DATABASE_URL / $DATABASE_URL).",
)
@click.option(
    "--allow-migrations",
    is_flag=True,
    help="Actually apply pending migrations. Without this flag the command is plan-only.",
)
@click.option(
    "--debug", is_flag=True, help="Re-raise on an unexpected failure for the full traceback."
)
def upgrade(database_url: str | None, allow_migrations: bool, debug: bool) -> None:
    """Apply pending database migrations — the single, gated owner of all DDL.

    This is the *only* sanctioned migration runner (§13.9): every phase that
    needs schema registers its idempotent, version-stamped migration in
    ``agentship_cli.migrations.REGISTERED_MIGRATIONS`` rather than shipping its
    own runner.

    **Plan-only by default.** With no ``--allow-migrations`` flag the command
    only *prints* what would run and touches no database — the apply path is
    unreachable, so ungated DDL is impossible by construction.

    **With ``--allow-migrations``** it applies the pending migrations in
    ``version`` order and prints an ``applied N migrations (M pending)``
    summary. If migrations are pending but no DSN is available (neither
    ``--database-url`` nor an environment variable), it refuses with a clean
    ``Error:`` naming the missing DSN. With zero registered migrations it
    succeeds as a no-op even without a DSN. On an unexpected failure it prints a
    concise ``Error:`` (no traceback) and exits ``1``; ``--debug`` re-raises.
    """
    migrations = _pending_migrations()

    if not allow_migrations:
        # Plan-only: never resolve or touch a database. The apply path below is
        # simply not reached, which is what makes ungated DDL impossible.
        _print_plan(migrations)
        click.echo("Plan-only — pass --allow-migrations to apply.")
        return

    try:
        _apply_migrations(migrations, database_url)
    except AgentShipError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)
    except Exception as exc:  # noqa: BLE001 - unexpected; surface cleanly or re-raise
        if debug:
            raise
        click.echo(f"Error: {exc} (run with --debug for the full traceback)", err=True)
        sys.exit(1)


def _apply_migrations(migrations: list[Migration], database_url: str | None) -> None:
    """Apply ``migrations`` in order against the resolved DSN.

    This is the *only* code path that may run DDL, and it is reachable only from
    ``upgrade`` when ``--allow-migrations`` was passed — that is the gate. With
    no pending migrations it is a pure no-op and needs no DSN. With pending
    migrations and no resolvable DSN it raises
    :class:`~agentship.errors.AgentShipError` naming the missing DSN.
    """
    if not migrations:
        click.echo("applied 0 migrations (0 pending)")
        return

    resolved = _resolve_database_url(database_url)
    if resolved is None:
        raise AgentShipError(
            "no database URL — pass --database-url or set "
            f"{DATABASE_URL_ENV_VARS[0]} (or {DATABASE_URL_ENV_VARS[1]})"
        )

    for migration in migrations:
        click.echo(f"applying {migration.version}  {migration.description}")
        migration.apply(resolved)

    click.echo(f"applied {len(migrations)} migrations (0 pending)")
