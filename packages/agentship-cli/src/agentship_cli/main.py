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
import os
import sys
from pathlib import Path

import click
from agentship.engines.base import ENGINES, assert_spec_supported
from agentship.errors import AgentShipError
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
def run(file: str, input_text: str, stream: bool, env_file: str | None, debug: bool) -> None:
    """Run one turn of the agent declared in FILE and print its output.

    Before running, environment variables are loaded from a ``.env`` — the current
    directory's ``.env`` by default, or the file named by ``--env-file`` — so a
    provider key such as ``OPENAI_API_KEY`` need not be exported by hand. An
    already-exported variable is never overwritten by the ``.env``.

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
    deepagents_reason = _check_deepagents_version(spec)
    if deepagents_reason is not None:
        return deepagents_reason
    return None


def _check_deepagents_version(spec) -> str | None:
    """Guard a ``template: deepagents`` spec against a missing/drifted deepagents install.

    deepagents is pre-1.0 (its ``create_deep_agent`` signature can drift), so a
    ``deepagents`` spec is only healthy when the pinned version is installed. This
    reads the langgraph adapter's version guard *if that adapter is importable*
    (``doctor`` runs in projects that may not have it), returning an actionable
    reason when deepagents is absent or the wrong version, or ``None`` when the spec
    is not a deepagents one or the install is fine. Never raises: an import failure
    just means the guard is skipped (the capability gate already vouched for the
    engine).
    """
    if getattr(spec, "template", None) != "deepagents":
        return None
    try:
        from agentship_langgraph.templates.deepagents_tpl import (
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
            "template 'deepagents' needs the deepagents package — "
            "pip install 'agentship-langgraph[deepagents]'"
        )
    return (
        f"template 'deepagents' is pinned to deepagents=={PINNED_DEEPAGENTS_VERSION} "
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


def _write_new_file(path: Path, text: str) -> None:
    """Write ``text`` to ``path``, refusing to overwrite an existing file.

    Scaffolding never clobbers a user's work: an existing target raises
    :class:`~agentship.errors.AgentShipError` so the caller reports a clean error
    instead of silently replacing content. Parent directories are created first.
    """
    if path.exists():
        raise AgentShipError(f"refusing to overwrite existing file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


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


@main.command(name="new-agent")
@click.argument("name")
@click.option(
    "--engine", default="langgraph", show_default=True, help="Engine the agent runs on."
)
@click.option(
    "--agents-dir",
    "agents_dir",
    type=click.Path(file_okay=False),
    default="agents",
    show_default=True,
    help="Directory to write the agent spec into.",
)
def new_agent(name: str, engine: str, agents_dir: str) -> None:
    """Scaffold one starter agent spec at ``<agents-dir>/NAME.yaml``.

    Writes a single-agent spec (a prompt plus, for the default ``langgraph``
    engine, a ``model`` line) so the agent runs as-is. The command refuses to
    overwrite an existing spec, reporting a clean ``Error:`` and exiting ``1``.
    """
    path = Path(agents_dir) / f"{name}.yaml"
    try:
        _write_new_file(path, scaffold.new_agent_yaml(name, engine))
    except AgentShipError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)
    click.echo(f"Wrote {path}")
    click.echo(f'Run it: agentship run {path} --input "hello"')


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
